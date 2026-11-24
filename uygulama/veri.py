import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import pymysql
from fastapi import HTTPException
from db import (
    db_yazma,
    db_okuma,
    redis_client,
    temizle_veri,
    sp_cagir,
    menu_onbellek_temizle,
)

logger = logging.getLogger("yemekhane")

OTURUM_SURESI = 60 * 30


def gun_sonuna_kalan_saniye() -> int:
    simdi = datetime.now()
    yarin_basi = (simdi + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    kalan = int((yarin_basi - simdi).total_seconds())
    return max(kalan, 60)


def saglik_kontrol() -> dict:
    sonuc = {"durum": "ok", "yazma_db": False, "okuma_db": False, "redis": False}
    with db_yazma() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            sonuc["yazma_db"] = cur.fetchone()["ok"] == 1
    with db_okuma() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            sonuc["okuma_db"] = cur.fetchone()["ok"] == 1
    sonuc["redis"] = bool(redis_client.ping())
    return sonuc


def oturum_olustur(kullanici_id: int, rol: str) -> str:
    token = secrets.token_hex(32)
    redis_key = f"oturum:{token}"
    redis_client.setex(
        redis_key,
        OTURUM_SURESI,
        json.dumps({"kullanici_id": kullanici_id, "rol": rol}),
    )
    logger.info(
        "[OTURUM OLUSTURULDU] Anahtar: %s | Kullanici ID: %s | TTL: %ds",
        redis_key,
        kullanici_id,
        OTURUM_SURESI,
    )
    return token


def oturum_getir_ve_yenile(token: str) -> Optional[dict]:
    redis_key = f"oturum:{token}"
    veri = redis_client.get(redis_key)
    if not veri:
        return None
    redis_client.expire(redis_key, OTURUM_SURESI)
    return json.loads(veri)


def oturum_sil(token: str) -> None:
    redis_key = f"oturum:{token}"
    redis_client.delete(redis_key)
    logger.info("[OTURUM SILINDI] Anahtar: %s", redis_key)


def kullanici_eposta_ile_bul(eposta: str) -> Optional[dict]:
    with db_okuma() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ad, soyad, eposta, sifre_hash, rol, ogrenci_no, bakiye, aktif "
                "FROM kullanicilar WHERE eposta = %s",
                (eposta.strip().lower(),),
            )
            return cur.fetchone()


def kullanici_alerjen_idleri(kullanici_id: int) -> List[int]:
    with db_yazma() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT alerjen_id FROM kullanici_alerjenleri "
                "WHERE kullanici_id = %s ORDER BY alerjen_id",
                (kullanici_id,),
            )
            return [r["alerjen_id"] for r in cur.fetchall()]


def kullanici_id_ile_getir(kullanici_id: int) -> Optional[dict]:
    with db_yazma() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ad, soyad, eposta, rol, ogrenci_no, bakiye, aktif "
                "FROM kullanicilar WHERE id = %s",
                (kullanici_id,),
            )
            kullanici = cur.fetchone()
            if not kullanici:
                return None
            cur.execute(
                "SELECT alerjen_id FROM kullanici_alerjenleri "
                "WHERE kullanici_id = %s ORDER BY alerjen_id",
                (kullanici["id"],),
            )
            kullanici["alerjen_idler"] = [r["alerjen_id"] for r in cur.fetchall()]
    return temizle_veri(kullanici)


def kullanici_alerjen_guncelle(kullanici_id: int, alerjen_idler: List[int]) -> List[int]:
    benzersiz_idler = sorted(set(alerjen_idler))
    conn = db_yazma()
    try:
        conn.autocommit(False)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM kullanici_alerjenleri WHERE kullanici_id = %s",
                (kullanici_id,),
            )
            for aid in benzersiz_idler:
                cur.execute(
                    "INSERT INTO kullanici_alerjenleri (kullanici_id, alerjen_id) VALUES (%s, %s)",
                    (kullanici_id, aid),
                )
        conn.commit()
        return benzersiz_idler
    except pymysql.IntegrityError:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Geçersiz alerjen seçimi.")
    finally:
        conn.close()


def filtre_secenekleri_getir() -> dict:
    with db_okuma() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, ad, konum, kapasite FROM yemekhaneler ORDER BY id")
            yemekhaneler = cur.fetchall()
            cur.execute(
                "SELECT id, ad, sira, son_saat, servis_baslangic, servis_bitis, yarim_porsiyon "
                "FROM ogunler ORDER BY sira"
            )
            ogunler = cur.fetchall()
            cur.execute("SELECT id, ad FROM alerjenler ORDER BY ad")
            alerjenler = cur.fetchall()
            cur.execute("SELECT DISTINCT tarih FROM menuler ORDER BY tarih")
            tarihler = [r["tarih"] for r in cur.fetchall()]
    return temizle_veri({
        "yemekhaneler": yemekhaneler,
        "ogunler": ogunler,
        "alerjenler": alerjenler,
        "tarihler": tarihler,
    })


def _detaylardan_menu_grupla(detaylar: list) -> dict:
    menuler_map = {}
    for satir in detaylar:
        mid = satir["menu_id"]
        if mid not in menuler_map:
            yarim_izin = bool(satir["yarim_porsiyon"])
            menuler_map[mid] = {
                "menu_id": mid,
                "tarih": satir["tarih"],
                "yemekhane_id": satir["yemekhane_id"],
                "yemekhane": satir["yemekhane"],
                "ogun_id": satir["ogun_id"],
                "ogun": satir["ogun"],
                "ogun_sira": satir["ogun_sira"],
                "yarim_porsiyon": yarim_izin,
                "rezervasyon_son": satir["rezervasyon_son"],
                "ogrenci_fiyati": satir["ogrenci_fiyati"],
                "personel_fiyati": satir["personel_fiyati"],
                "ogrenci_yarim_fiyati": round(satir["ogrenci_fiyati"] * 0.60, 2) if yarim_izin else None,
                "personel_yarim_fiyati": round(satir["personel_fiyati"] * 0.60, 2) if yarim_izin else None,
                "toplam_kalori": 0,
                "yemekler": [],
            }
        alerjen_id_list = (
            [int(x) for x in satir["alerjen_idler"].split(",")]
            if satir.get("alerjen_idler")
            else []
        )
        menuler_map[mid]["yemekler"].append({
            "yemek_id": satir["yemek_id"],
            "yemek": satir["yemek"],
            "kategori": satir["kategori"],
            "kategori_sira": satir["kategori_sira"],
            "kalori": satir["kalori"],
            "vejetaryen": bool(satir["vejetaryen"]),
            "alerjenler": satir["alerjenler"] or "",
            "alerjen_idler": alerjen_id_list,
        })
        menuler_map[mid]["toplam_kalori"] += satir["kalori"]
    return menuler_map


def _canli_doluluk_ve_puan_ekle(menu_listesi: list) -> list:
    if not menu_listesi:
        return []
    menu_idler = [m["menu_id"] for m in menu_listesi]
    yer_tutucu = ",".join(["%s"] * len(menu_idler))

    with db_okuma() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM v_menu_doluluk WHERE menu_id IN ({yer_tutucu})",
                tuple(menu_idler),
            )
            doluluk_map = {r["menu_id"]: temizle_veri(r) for r in cur.fetchall()}

            cur.execute(
                f"SELECT * FROM v_menu_puan WHERE menu_id IN ({yer_tutucu})",
                tuple(menu_idler),
            )
            puan_map = {r["menu_id"]: temizle_veri(r) for r in cur.fetchall()}

    zengin = []
    for m in menu_listesi:
        mid = m["menu_id"]
        dol = doluluk_map.get(mid, {})
        puan = puan_map.get(mid, {})
        yeni = dict(m)
        yeni["kapasite"] = dol.get("kapasite", 0)
        yeni["rezerve"] = dol.get("rezerve", 0)
        yeni["bos_yer"] = dol.get("bos_yer", 0)
        yeni["doluluk_yuzde"] = dol.get("doluluk_yuzde", 0.0)
        yeni["oy_sayisi"] = puan.get("oy_sayisi", 0)
        yeni["ortalama_puan"] = puan.get("ortalama_puan")
        zengin.append(yeni)
    return zengin


def menuleri_getir(
    tarih: Optional[str] = None,
    yemekhane_id: Optional[int] = None,
    ogun_id: Optional[int] = None,
    baslangic_tarih: Optional[str] = None,
    bitis_tarih: Optional[str] = None,
    vejetaryen: Optional[bool] = None,
    haric_alerjen: Optional[str] = None,
) -> Tuple[list, str]:
    menu_listesi = []
    cache_durum = "MISS"

    if yemekhane_id and tarih and ogun_id and not baslangic_tarih and not bitis_tarih:
        cache_key = f"menu:{yemekhane_id}:{tarih}:{ogun_id}"
        onbellek = redis_client.get(cache_key)
        if onbellek:
            menu_listesi = [json.loads(onbellek)]
            cache_durum = "HIT"
            logger.info("[MENU ONBELLEK HIT] %s -> Redis'ten getirildi", cache_key)
        else:
            logger.info("[MENU ONBELLEK MISS] %s -> MySQL replikadan okunuyor", cache_key)
            with db_okuma() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM v_menu_detay "
                        "WHERE yemekhane_id = %s AND tarih = %s AND ogun_id = %s "
                        "ORDER BY kategori_sira, yemek",
                        (yemekhane_id, tarih, ogun_id),
                    )
                    detaylar = temizle_veri(cur.fetchall())
            gruplu = _detaylardan_menu_grupla(detaylar)
            menu_listesi = list(gruplu.values())
            if menu_listesi:
                ttl = gun_sonuna_kalan_saniye()
                redis_client.setex(
                    cache_key,
                    ttl,
                    json.dumps(menu_listesi[0], ensure_ascii=False),
                )
                logger.info("[MENU ONBELLEK SET] %s | TTL: %ds (gun sonuna kadar)", cache_key, ttl)
            cache_durum = "MISS"
    else:
        kosullar = []
        parametreler = []
        if tarih:
            kosullar.append("m.tarih = %s")
            parametreler.append(tarih)
        if baslangic_tarih:
            kosullar.append("m.tarih >= %s")
            parametreler.append(baslangic_tarih)
        if bitis_tarih:
            kosullar.append("m.tarih <= %s")
            parametreler.append(bitis_tarih)
        if yemekhane_id:
            kosullar.append("m.yemekhane_id = %s")
            parametreler.append(yemekhane_id)
        if ogun_id:
            kosullar.append("m.ogun_id = %s")
            parametreler.append(ogun_id)

        where_sql = ("WHERE " + " AND ".join(kosullar)) if kosullar else ""
        with db_okuma() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT m.id, m.yemekhane_id, m.tarih, m.ogun_id "
                    f"FROM menuler m JOIN ogunler o ON o.id = m.ogun_id {where_sql} "
                    f"ORDER BY m.tarih, m.yemekhane_id, o.sira",
                    tuple(parametreler) if parametreler else None,
                )
                satirlar = temizle_veri(cur.fetchall())

        if satirlar:
            anahtarlar = [
                f"menu:{r['yemekhane_id']}:{r['tarih']}:{r['ogun_id']}"
                for r in satirlar
            ]
            onbellekler = redis_client.mget(anahtarlar)
            eksik_idler = [
                satirlar[i]["id"]
                for i, v in enumerate(onbellekler)
                if v is None
            ]
            taze_map = {}
            if eksik_idler:
                yer_tutucu = ",".join(["%s"] * len(eksik_idler))
                with db_okuma() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"SELECT * FROM v_menu_detay WHERE menu_id IN ({yer_tutucu}) "
                            "ORDER BY tarih, yemekhane_id, ogun_sira, kategori_sira, yemek",
                            tuple(eksik_idler),
                        )
                        detaylar = temizle_veri(cur.fetchall())
                taze_map = _detaylardan_menu_grupla(detaylar)
                ttl = gun_sonuna_kalan_saniye()
                for m in taze_map.values():
                    k = f"menu:{m['yemekhane_id']}:{m['tarih']}:{m['ogun_id']}"
                    redis_client.setex(k, ttl, json.dumps(m, ensure_ascii=False))
                cache_durum = "MISS"
                logger.info("[MENU ONBELLEK MISS] %d menu replikadan okunup Redis'e yazildi", len(taze_map))
            else:
                cache_durum = "HIT"
                logger.info("[MENU ONBELLEK HIT] %d menu Redis'ten getirildi", len(satirlar))

            for i, r in enumerate(satirlar):
                if onbellekler[i] is not None:
                    menu_listesi.append(json.loads(onbellekler[i]))
                elif r["id"] in taze_map:
                    menu_listesi.append(taze_map[r["id"]])

    zengin_liste = _canli_doluluk_ve_puan_ekle(menu_listesi)

    haric_idler = set()
    if haric_alerjen:
        haric_idler = {int(x) for x in haric_alerjen.split(",") if x.strip().isdigit()}

    sonuc = []
    for m in zengin_liste:
        yemekler = m["yemekler"]
        if vejetaryen:
            yemekler = [y for y in yemekler if y["vejetaryen"]]
        if haric_idler:
            yemekler = [y for y in yemekler if not (haric_idler & set(y["alerjen_idler"]))]
        if yemekler:
            yeni_m = dict(m)
            yeni_m["yemekler"] = yemekler
            yeni_m["toplam_kalori"] = sum(y["kalori"] for y in yemekler)
            sonuc.append(yeni_m)

    return sonuc, cache_durum


def menu_degerlendirmeleri_getir(menu_id: int) -> list:
    with db_okuma() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id, d.puan, d.yorum, d.olusturma,
                       CONCAT(k.ad, ' ', LEFT(k.soyad, 1), '.') AS kullanici
                FROM degerlendirmeler d
                JOIN kullanicilar k ON k.id = d.kullanici_id
                WHERE d.menu_id = %s
                ORDER BY d.olusturma DESC
                """,
                (menu_id,),
            )
            return temizle_veri(cur.fetchall())


def kullanici_rezervasyonlari_getir(kullanici_id: int) -> list:
    with db_yazma() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.menu_id, r.durum, r.porsiyon, r.ucret, r.olusturma,
                       r.kullanma_zamani, r.iptal_zamani,
                       m.tarih, yh.ad AS yemekhane, o.ad AS ogun,
                       TIMESTAMP(m.tarih, o.son_saat) AS rezervasyon_son,
                       d.id AS degerlendirme_id, d.puan AS verilen_puan, d.yorum AS verilen_yorum
                FROM rezervasyonlar r
                JOIN menuler m ON m.id = r.menu_id
                JOIN yemekhaneler yh ON yh.id = m.yemekhane_id
                JOIN ogunler o ON o.id = m.ogun_id
                LEFT JOIN degerlendirmeler d
                       ON d.menu_id = r.menu_id AND d.kullanici_id = r.kullanici_id
                WHERE r.kullanici_id = %s
                ORDER BY m.tarih DESC, o.sira ASC
                """,
                (kullanici_id,),
            )
            return temizle_veri(cur.fetchall())


def rezervasyon_olustur(kullanici_id: int, menu_id: int, porsiyon: str) -> dict:
    return sp_cagir("sp_rezervasyon_yap", (kullanici_id, menu_id, porsiyon))


def rezervasyon_iptal_et(kullanici_id: int, rezervasyon_id: int) -> dict:
    return sp_cagir("sp_rezervasyon_iptal", (kullanici_id, rezervasyon_id))


def bakiye_yukle(kullanici_id: int, tutar: float) -> dict:
    return sp_cagir("sp_bakiye_yukle", (kullanici_id, tutar))


def bakiye_hareketleri_getir(kullanici_id: int) -> list:
    with db_yazma() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, tur, tutar, rezervasyon_id, aciklama, olusturma
                FROM bakiye_hareketleri
                WHERE kullanici_id = %s
                ORDER BY olusturma DESC, id DESC
                LIMIT 50
                """,
                (kullanici_id,),
            )
            return temizle_veri(cur.fetchall())


def degerlendirme_ekle(kullanici_id: int, menu_id: int, puan: int, yorum: Optional[str]) -> None:
    try:
        with db_yazma() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT durum FROM rezervasyonlar WHERE kullanici_id = %s AND menu_id = %s",
                    (kullanici_id, menu_id),
                )
                rez = cur.fetchone()
                if not rez or rez["durum"] != "kullanildi":
                    raise HTTPException(
                        status_code=400,
                        detail="Yalnızca kullanılmış rezervasyonunuz bulunan menüleri değerlendirebilirsiniz.",
                    )
                cur.execute(
                    "INSERT INTO degerlendirmeler (kullanici_id, menu_id, puan, yorum) "
                    "VALUES (%s, %s, %s, %s)",
                    (kullanici_id, menu_id, puan, yorum),
                )
    except pymysql.IntegrityError as e:
        if e.args and e.args[0] == 1062:
            raise HTTPException(status_code=400, detail="Bu menüyü zaten değerlendirdiniz.")
        raise HTTPException(status_code=400, detail="Değerlendirme kaydedilemedi.")


def yonetim_yemekleri_getir() -> list:
    with db_okuma() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT y.id, y.ad, y.kalori, y.vejetaryen,
                       k.id AS kategori_id, k.ad AS kategori, k.sira AS kategori_sira
                FROM yemekler y
                JOIN yemek_kategorileri k ON k.id = y.kategori_id
                ORDER BY k.sira, y.ad
                """
            )
            return temizle_veri(cur.fetchall())


def yonetim_kullanicilari_getir() -> list:
    with db_okuma() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ad, soyad, eposta, rol, ogrenci_no, bakiye, aktif "
                "FROM kullanicilar ORDER BY rol, ad, soyad"
            )
            return temizle_veri(cur.fetchall())


def yonetim_menu_ekle(
    yemekhane_id: int,
    tarih: str,
    ogun_id: int,
    ogrenci_fiyati: float,
    personel_fiyati: float,
    yemek_idler: List[int],
) -> int:
    conn = db_yazma()
    try:
        conn.autocommit(False)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO menuler (yemekhane_id, tarih, ogun_id, ogrenci_fiyati, personel_fiyati)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (yemekhane_id, tarih, ogun_id, ogrenci_fiyati, personel_fiyati),
            )
            menu_id = cur.lastrowid
            for yid in yemek_idler:
                cur.execute(
                    "INSERT INTO menu_yemekleri (menu_id, yemek_id) VALUES (%s, %s)",
                    (menu_id, yid),
                )
        conn.commit()
        ozel = f"menu:{yemekhane_id}:{tarih}:{ogun_id}"
        silinen = menu_onbellek_temizle(ozel)
        logger.info("[REDIS INVALIDATE] Neden: Yeni menu eklendi (%s) | Silinen: %d", ozel, silinen)
        return menu_id
    except pymysql.IntegrityError as e:
        conn.rollback()
        if e.args and e.args[0] == 1062:
            raise HTTPException(
                status_code=400,
                detail="Bu yemekhane, tarih ve öğün için zaten bir menü tanımlı.",
            )
        raise HTTPException(status_code=400, detail="Menü eklenemedi, seçimleri kontrol edin.")
    finally:
        conn.close()


def yonetim_menu_guncelle(
    menu_id: int,
    ogrenci_fiyati: float,
    personel_fiyati: float,
    yemek_idler: List[int],
) -> None:
    conn = db_yazma()
    try:
        conn.autocommit(False)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, yemekhane_id, tarih, ogun_id FROM menuler WHERE id = %s",
                (menu_id,),
            )
            mevcut = temizle_veri(cur.fetchone())
            if not mevcut:
                raise HTTPException(status_code=404, detail="Menü bulunamadı.")

            cur.execute(
                "UPDATE menuler SET ogrenci_fiyati = %s, personel_fiyati = %s WHERE id = %s",
                (ogrenci_fiyati, personel_fiyati, menu_id),
            )
            cur.execute("DELETE FROM menu_yemekleri WHERE menu_id = %s", (menu_id,))
            for yid in yemek_idler:
                cur.execute(
                    "INSERT INTO menu_yemekleri (menu_id, yemek_id) VALUES (%s, %s)",
                    (menu_id, yid),
                )
        conn.commit()
        ozel = f"menu:{mevcut['yemekhane_id']}:{mevcut['tarih']}:{mevcut['ogun_id']}"
        silinen = menu_onbellek_temizle(ozel)
        logger.info(
            "[REDIS INVALIDATE] Neden: Menu guncellendi (menu_id=%d, %s) | Silinen: %d",
            menu_id,
            ozel,
            silinen,
        )
    except HTTPException:
        conn.rollback()
        raise
    except pymysql.MySQLError:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Menü güncellenemedi.")
    finally:
        conn.close()


def yonetim_menu_sil(menu_id: int) -> None:
    try:
        with db_yazma() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT yemekhane_id, tarih, ogun_id FROM menuler WHERE id = %s",
                    (menu_id,),
                )
                mevcut = temizle_veri(cur.fetchone())
                cur.execute("DELETE FROM menuler WHERE id = %s", (menu_id,))
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Menü bulunamadı.")
        if mevcut:
            ozel = f"menu:{mevcut['yemekhane_id']}:{mevcut['tarih']}:{mevcut['ogun_id']}"
            silinen = menu_onbellek_temizle(ozel)
            logger.info(
                "[REDIS INVALIDATE] Neden: Menu silindi (menu_id=%d, %s) | Silinen: %d",
                menu_id,
                ozel,
                silinen,
            )
    except pymysql.IntegrityError as e:
        if e.args and e.args[0] == 1451:
            raise HTTPException(status_code=400, detail="Rezervasyonu bulunan menü silinemez.")
        raise HTTPException(status_code=400, detail="Menü silinemedi.")


def yonetim_turnike_gecis(kullanici_id: int, yemekhane_id: int, zaman: Optional[str]) -> dict:
    return sp_cagir("sp_turnike_gecis", (kullanici_id, yemekhane_id, zaman))


def yonetim_ozet_getir() -> dict:
    with db_okuma() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.menu_id, d.tarih, d.yemekhane, d.ogun,
                       d.kapasite, d.rezerve, d.bos_yer, d.doluluk_yuzde,
                       p.oy_sayisi, p.ortalama_puan
                FROM v_menu_doluluk d
                LEFT JOIN v_menu_puan p ON p.menu_id = d.menu_id
                ORDER BY d.tarih DESC, d.yemekhane_id ASC, d.ogun_id ASC
                LIMIT 30
                """
            )
            doluluklar = cur.fetchall()

            cur.execute(
                """
                SELECT durum, COUNT(*) AS adet
                FROM rezervasyonlar
                GROUP BY durum
                """
            )
            durum_sayilari = cur.fetchall()

            cur.execute(
                """
                SELECT COUNT(*) AS toplam_kullanici,
                       SUM(CASE WHEN rol = 'ogrenci' THEN 1 ELSE 0 END) AS ogrenci_sayisi,
                       SUM(CASE WHEN rol = 'personel' THEN 1 ELSE 0 END) AS personel_sayisi,
                       ROUND(SUM(bakiye), 2) AS toplam_bakiye
                FROM kullanicilar
                """
            )
            genel = cur.fetchone()

    return temizle_veri({
        "genel": genel,
        "durum_sayilari": durum_sayilari,
        "doluluklar": doluluklar,
    })