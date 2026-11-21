import json
import secrets
from typing import List, Optional
import bcrypt
import pymysql
from fastapi import FastAPI, HTTPException, Request, Response, Depends, Query
from pydantic import BaseModel
from db import (
    db_yazma,
    db_okuma,
    redis_client,
    temizle_veri,
    sp_cagir,
    menu_onbellek_temizle,
)

app = FastAPI(
    title="Yemekhane Otomasyonu API",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

OTURUM_SURESI = 60 * 60 * 24  # 24 saat
MENU_CACHE_TTL = 120          # 2 dakika


# ---------------------------------------------------------------------
# Istek Modelleri
# ---------------------------------------------------------------------
class GirisIstegi(BaseModel):
    eposta: str
    sifre: str


class RezervasyonIstegi(BaseModel):
    menu_id: int


class BakiyeYukleIstegi(BaseModel):
    tutar: float


class DegerlendirmeIstegi(BaseModel):
    menu_id: int
    puan: int
    yorum: Optional[str] = None


class TurnikeIstegi(BaseModel):
    kullanici_id: int
    yemekhane_id: int
    zaman: Optional[str] = None


class MenuOlusturIstegi(BaseModel):
    yemekhane_id: int
    tarih: str
    ogun_id: int
    ogrenci_fiyati: float
    personel_fiyati: float
    yemek_idler: List[int]


class MenuGuncelleIstegi(BaseModel):
    ogrenci_fiyati: float
    personel_fiyati: float
    yemek_idler: List[int]


# ---------------------------------------------------------------------
# Yetkilendirme Yardimcilari
# ---------------------------------------------------------------------
def mevcut_kullanici(request: Request):
    token = request.cookies.get("oturum_id")
    if not token:
        raise HTTPException(status_code=401, detail="Giriş yapmanız gerekiyor.")
    veri = redis_client.get(f"oturum:{token}")
    if not veri:
        raise HTTPException(status_code=401, detail="Oturum süreniz doldu, tekrar giriş yapın.")
    oturum = json.loads(veri)

    # Read-your-own-writes: Kullanicinin guncel bakiyesi her zaman primary'den okunur
    with db_yazma() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ad, soyad, eposta, rol, ogrenci_no, bakiye, aktif "
                "FROM kullanicilar WHERE id = %s",
                (oturum["kullanici_id"],),
            )
            kullanici = cur.fetchone()
    if not kullanici or not kullanici["aktif"]:
        raise HTTPException(status_code=401, detail="Hesabınız aktif değil.")
    return temizle_veri(kullanici)


def yonetici_gerekli(kullanici=Depends(mevcut_kullanici)):
    if kullanici["rol"] != "yonetici":
        raise HTTPException(status_code=403, detail="Bu işlem için yönetici yetkisi gerekir.")
    return kullanici


# ---------------------------------------------------------------------
# Saglik ve Oturum
# ---------------------------------------------------------------------
@app.get("/api/saglik")
def saglik():
    sonuc = {"durum": "ok", "yazma_db": False, "okuma_db": False, "redis": False}
    with db_yazma() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            sonuc["yazma_db"] = cur.fetchone()["ok"] == 1
    with db_okuma() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            sonuc["okuma_db"] = cur.fetchone()["ok"] == 1
    sonuc["redis"] = redis_client.ping()
    return sonuc


@app.post("/api/giris")
def giris(istek: GirisIstegi, response: Response):
    with db_okuma() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ad, soyad, eposta, sifre_hash, rol, ogrenci_no, bakiye, aktif "
                "FROM kullanicilar WHERE eposta = %s",
                (istek.eposta.strip().lower(),),
            )
            kullanici = cur.fetchone()

    if not kullanici or not bcrypt.checkpw(
        istek.sifre.encode("utf-8"), kullanici["sifre_hash"].encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="E-posta veya şifre hatalı.")
    if not kullanici["aktif"]:
        raise HTTPException(status_code=403, detail="Hesabınız pasif durumda.")

    token = secrets.token_hex(32)
    redis_client.setex(
        f"oturum:{token}",
        OTURUM_SURESI,
        json.dumps({"kullanici_id": kullanici["id"], "rol": kullanici["rol"]}),
    )
    response.set_cookie(
        key="oturum_id",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=OTURUM_SURESI,
        path="/",
    )

    kullanici.pop("sifre_hash", None)
    return {"mesaj": "Giriş başarılı.", "kullanici": temizle_veri(kullanici)}


@app.post("/api/cikis")
def cikis(request: Request, response: Response):
    token = request.cookies.get("oturum_id")
    if token:
        redis_client.delete(f"oturum:{token}")
    response.delete_cookie("oturum_id", path="/")
    return {"mesaj": "Çıkış yapıldı."}


@app.get("/api/ben")
def ben(kullanici=Depends(mevcut_kullanici)):
    return kullanici


# ---------------------------------------------------------------------
# Filtreler ve Menuler (Okuma -> Replica + Redis Onbellek)
# ---------------------------------------------------------------------
@app.get("/api/filtreler")
def filtreler():
    with db_okuma() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, ad, konum, kapasite FROM yemekhaneler ORDER BY id")
            yemekhaneler = cur.fetchall()
            cur.execute(
                "SELECT id, ad, sira, son_saat, servis_baslangic, servis_bitis "
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


@app.get("/api/menuler")
def menuler(
    response: Response,
    tarih: Optional[str] = Query(None),
    yemekhane_id: Optional[int] = Query(None),
    ogun_id: Optional[int] = Query(None),
    vejetaryen: Optional[bool] = Query(None),
    haric_alerjen: Optional[str] = Query(None),
):
    cache_key = f"menu:{tarih or 'hepsi'}:{yemekhane_id or 'hepsi'}:{ogun_id or 'hepsi'}"
    onbellek = redis_client.get(cache_key)

    if onbellek:
        menu_listesi = json.loads(onbellek)
        response.headers["X-Cache"] = "HIT"
    else:
        kosullar = []
        parametreler = []
        if tarih:
            kosullar.append("tarih = %s")
            parametreler.append(tarih)
        if yemekhane_id:
            kosullar.append("yemekhane_id = %s")
            parametreler.append(yemekhane_id)
        if ogun_id:
            kosullar.append("ogun_id = %s")
            parametreler.append(ogun_id)

        where_sql = ("WHERE " + " AND ".join(kosullar)) if kosullar else ""

        with db_okuma() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM v_menu_detay {where_sql} "
                    "ORDER BY tarih, yemekhane_id, ogun_sira, kategori_sira, yemek",
                    tuple(parametreler),
                )
                detaylar = temizle_veri(cur.fetchall())

                cur.execute(
                    f"SELECT * FROM v_menu_doluluk {where_sql}",
                    tuple(parametreler),
                )
                doluluk_map = {r["menu_id"]: temizle_veri(r) for r in cur.fetchall()}

                cur.execute("SELECT * FROM v_menu_puan")
                puan_map = {r["menu_id"]: temizle_veri(r) for r in cur.fetchall()}

        menuler_map = {}
        for satir in detaylar:
            mid = satir["menu_id"]
            if mid not in menuler_map:
                dol = doluluk_map.get(mid, {})
                puan = puan_map.get(mid, {})
                menuler_map[mid] = {
                    "menu_id": mid,
                    "tarih": satir["tarih"],
                    "yemekhane_id": satir["yemekhane_id"],
                    "yemekhane": satir["yemekhane"],
                    "ogun_id": satir["ogun_id"],
                    "ogun": satir["ogun"],
                    "ogun_sira": satir["ogun_sira"],
                    "rezervasyon_son": satir["rezervasyon_son"],
                    "ogrenci_fiyati": satir["ogrenci_fiyati"],
                    "personel_fiyati": satir["personel_fiyati"],
                    "kapasite": dol.get("kapasite", 0),
                    "rezerve": dol.get("rezerve", 0),
                    "bos_yer": dol.get("bos_yer", 0),
                    "doluluk_yuzde": dol.get("doluluk_yuzde", 0.0),
                    "oy_sayisi": puan.get("oy_sayisi", 0),
                    "ortalama_puan": puan.get("ortalama_puan"),
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

        menu_listesi = list(menuler_map.values())
        redis_client.setex(cache_key, MENU_CACHE_TTL, json.dumps(menu_listesi, ensure_ascii=False))
        response.headers["X-Cache"] = "MISS"

    haric_idler = set()
    if haric_alerjen:
        haric_idler = {int(x) for x in haric_alerjen.split(",") if x.strip().isdigit()}

    sonuc = []
    for m in menu_listesi:
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

    return sonuc


@app.get("/api/menuler/{menu_id}/degerlendirmeler")
def menu_degerlendirmeleri(menu_id: int):
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


# ---------------------------------------------------------------------
# Rezervasyonlar (Yazma -> Stored Procedure, Kullanicinin Kendi Listesi -> Primary)
# ---------------------------------------------------------------------
@app.get("/api/rezervasyonlar")
def rezervasyonlarim(kullanici=Depends(mevcut_kullanici)):
    with db_yazma() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.menu_id, r.durum, r.ucret, r.olusturma,
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
                (kullanici["id"],),
            )
            return temizle_veri(cur.fetchall())


@app.post("/api/rezervasyonlar")
def rezervasyon_yap(istek: RezervasyonIstegi, kullanici=Depends(mevcut_kullanici)):
    sonuc = sp_cagir("sp_rezervasyon_yap", (kullanici["id"], istek.menu_id))
    menu_onbellek_temizle()
    return {"mesaj": "Rezervasyonunuz oluşturuldu.", **sonuc}


@app.post("/api/rezervasyonlar/{rezervasyon_id}/iptal")
def rezervasyon_iptal(rezervasyon_id: int, kullanici=Depends(mevcut_kullanici)):
    sonuc = sp_cagir("sp_rezervasyon_iptal", (kullanici["id"], rezervasyon_id))
    menu_onbellek_temizle()
    return {"mesaj": "Rezervasyon iptal edildi ve ücret iade edildi.", **sonuc}


# ---------------------------------------------------------------------
# Bakiye
# ---------------------------------------------------------------------
@app.post("/api/bakiye/yukle")
def bakiye_yukle(istek: BakiyeYukleIstegi, kullanici=Depends(mevcut_kullanici)):
    sonuc = sp_cagir("sp_bakiye_yukle", (kullanici["id"], istek.tutar))
    return {"mesaj": "Bakiye başarıyla yüklendi.", **sonuc}


@app.get("/api/bakiye/hareketler")
def bakiye_hareketleri(kullanici=Depends(mevcut_kullanici)):
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
                (kullanici["id"],),
            )
            return temizle_veri(cur.fetchall())


# ---------------------------------------------------------------------
# Degerlendirmeler (Yalnizca kullanilmis rezervasyonu olan menuler)
# ---------------------------------------------------------------------
@app.post("/api/degerlendirmeler")
def degerlendirme_ekle(istek: DegerlendirmeIstegi, kullanici=Depends(mevcut_kullanici)):
    if istek.puan < 1 or istek.puan > 5:
        raise HTTPException(status_code=400, detail="Puan 1 ile 5 arasında olmalıdır.")

    yorum = (istek.yorum or "").strip() or None
    if yorum and len(yorum) > 500:
        raise HTTPException(status_code=400, detail="Yorum en fazla 500 karakter olabilir.")

    try:
        with db_yazma() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT durum FROM rezervasyonlar WHERE kullanici_id = %s AND menu_id = %s",
                    (kullanici["id"], istek.menu_id),
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
                    (kullanici["id"], istek.menu_id, istek.puan, yorum),
                )
        menu_onbellek_temizle()
        return {"mesaj": "Değerlendirmeniz kaydedildi."}
    except pymysql.IntegrityError as e:
        if e.args and e.args[0] == 1062:
            raise HTTPException(status_code=400, detail="Bu menüyü zaten değerlendirdiniz.")
        raise HTTPException(status_code=400, detail="Değerlendirme kaydedilemedi.")


# ---------------------------------------------------------------------
# Yonetici Paneli Uclari
# ---------------------------------------------------------------------
@app.get("/api/yonetim/yemekler")
def yonetim_yemekler(_=Depends(yonetici_gerekli)):
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


@app.get("/api/yonetim/kullanicilar")
def yonetim_kullanicilar(_=Depends(yonetici_gerekli)):
    with db_okuma() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ad, soyad, eposta, rol, ogrenci_no, bakiye, aktif "
                "FROM kullanicilar ORDER BY rol, ad, soyad"
            )
            return temizle_veri(cur.fetchall())


@app.post("/api/yonetim/menuler")
def yonetim_menu_ekle(istek: MenuOlusturIstegi, _=Depends(yonetici_gerekli)):
    if istek.ogrenci_fiyati < 0 or istek.personel_fiyati < 0:
        raise HTTPException(status_code=400, detail="Fiyatlar negatif olamaz.")
    yemek_idler = list(dict.fromkeys(istek.yemek_idler))
    if not yemek_idler:
        raise HTTPException(status_code=400, detail="Menü için en az bir yemek seçmelisiniz.")

    conn = db_yazma()
    try:
        conn.autocommit(False)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO menuler (yemekhane_id, tarih, ogun_id, ogrenci_fiyati, personel_fiyati)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    istek.yemekhane_id,
                    istek.tarih,
                    istek.ogun_id,
                    istek.ogrenci_fiyati,
                    istek.personel_fiyati,
                ),
            )
            menu_id = cur.lastrowid
            for yid in yemek_idler:
                cur.execute(
                    "INSERT INTO menu_yemekleri (menu_id, yemek_id) VALUES (%s, %s)",
                    (menu_id, yid),
                )
        conn.commit()
        menu_onbellek_temizle()
        return {"mesaj": "Yeni menü eklendi.", "menu_id": menu_id}
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


@app.put("/api/yonetim/menuler/{menu_id}")
def yonetim_menu_guncelle(menu_id: int, istek: MenuGuncelleIstegi, _=Depends(yonetici_gerekli)):
    if istek.ogrenci_fiyati < 0 or istek.personel_fiyati < 0:
        raise HTTPException(status_code=400, detail="Fiyatlar negatif olamaz.")
    yemek_idler = list(dict.fromkeys(istek.yemek_idler))
    if not yemek_idler:
        raise HTTPException(status_code=400, detail="Menüde en az bir yemek olmalıdır.")

    conn = db_yazma()
    try:
        conn.autocommit(False)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM menuler WHERE id = %s", (menu_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Menü bulunamadı.")

            cur.execute(
                "UPDATE menuler SET ogrenci_fiyati = %s, personel_fiyati = %s WHERE id = %s",
                (istek.ogrenci_fiyati, istek.personel_fiyati, menu_id),
            )
            cur.execute("DELETE FROM menu_yemekleri WHERE menu_id = %s", (menu_id,))
            for yid in yemek_idler:
                cur.execute(
                    "INSERT INTO menu_yemekleri (menu_id, yemek_id) VALUES (%s, %s)",
                    (menu_id, yid),
                )
        conn.commit()
        menu_onbellek_temizle()
        return {"mesaj": "Menü güncellendi."}
    except HTTPException:
        conn.rollback()
        raise
    except pymysql.MySQLError:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Menü güncellenemedi.")
    finally:
        conn.close()


@app.delete("/api/yonetim/menuler/{menu_id}")
def yonetim_menu_sil(menu_id: int, _=Depends(yonetici_gerekli)):
    try:
        with db_yazma() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM menuler WHERE id = %s", (menu_id,))
                if cur.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Menü bulunamadı.")
        menu_onbellek_temizle()
        return {"mesaj": "Menü silindi."}
    except pymysql.IntegrityError as e:
        if e.args and e.args[0] == 1451:
            raise HTTPException(status_code=400, detail="Rezervasyonu bulunan menü silinemez.")
        raise HTTPException(status_code=400, detail="Menü silinemedi.")


@app.post("/api/yonetim/turnike")
def yonetim_turnike(istek: TurnikeIstegi, _=Depends(yonetici_gerekli)):
    zaman = (istek.zaman or "").strip() or None
    sonuc = sp_cagir("sp_turnike_gecis", (istek.kullanici_id, istek.yemekhane_id, zaman))
    return {"mesaj": "Turnike geçişi onaylandı.", **sonuc}


@app.get("/api/yonetim/ozet")
def yonetim_ozet(_=Depends(yonetici_gerekli)):
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