import logging
from typing import List, Optional
import bcrypt
from fastapi import FastAPI, HTTPException, Request, Response, Depends, Query
from pydantic import BaseModel
import veri
from db import temizle_veri

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

app = FastAPI(
    title="Yemekhane Otomasyonu API",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


class GirisIstegi(BaseModel):
    eposta: str
    sifre: str


class RezervasyonIstegi(BaseModel):
    menu_id: int
    porsiyon: str = "tam"


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


class AlerjenGuncelleIstegi(BaseModel):
    alerjen_idler: List[int]


def mevcut_kullanici(request: Request, response: Response):
    token = request.cookies.get("oturum_id")
    if not token:
        raise HTTPException(status_code=401, detail="Giriş yapmanız gerekiyor.")
    oturum = veri.oturum_getir_ve_yenile(token)
    if not oturum:
        raise HTTPException(status_code=401, detail="Oturum süreniz doldu, tekrar giriş yapın.")

    response.set_cookie(
        key="oturum_id",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=veri.OTURUM_SURESI,
        path="/",
    )
    kullanici = veri.kullanici_id_ile_getir(oturum["kullanici_id"])
    if not kullanici or not kullanici["aktif"]:
        raise HTTPException(status_code=401, detail="Hesabınız aktif değil.")
    return kullanici


def yonetici_gerekli(kullanici=Depends(mevcut_kullanici)):
    if kullanici["rol"] != "yonetici":
        raise HTTPException(status_code=403, detail="Bu işlem için yönetici yetkisi gerekir.")
    return kullanici


@app.get("/api/saglik")
def saglik():
    return veri.saglik_kontrol()


@app.post("/api/giris")
def giris(istek: GirisIstegi, response: Response):
    kullanici = veri.kullanici_eposta_ile_bul(istek.eposta)
    if not kullanici or not bcrypt.checkpw(
        istek.sifre.encode("utf-8"), kullanici["sifre_hash"].encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="E-posta veya şifre hatalı.")
    if not kullanici["aktif"]:
        raise HTTPException(status_code=403, detail="Hesabınız pasif durumda.")

    token = veri.oturum_olustur(kullanici["id"], kullanici["rol"])
    response.set_cookie(
        key="oturum_id",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=veri.OTURUM_SURESI,
        path="/",
    )

    kullanici.pop("sifre_hash", None)
    kullanici["alerjen_idler"] = veri.kullanici_alerjen_idleri(kullanici["id"])
    return {"mesaj": "Giriş başarılı.", "kullanici": temizle_veri(kullanici)}


@app.post("/api/cikis")
def cikis(request: Request, response: Response):
    token = request.cookies.get("oturum_id")
    if token:
        veri.oturum_sil(token)
    response.delete_cookie("oturum_id", path="/")
    return {"mesaj": "Çıkış yapıldı."}


@app.get("/api/ben")
def ben(kullanici=Depends(mevcut_kullanici)):
    return kullanici


@app.get("/api/profil/alerjenler")
def profil_alerjenler(kullanici=Depends(mevcut_kullanici)):
    return {"alerjen_idler": kullanici["alerjen_idler"]}


@app.put("/api/profil/alerjenler")
def profil_alerjen_guncelle(istek: AlerjenGuncelleIstegi, kullanici=Depends(mevcut_kullanici)):
    guncel = veri.kullanici_alerjen_guncelle(kullanici["id"], istek.alerjen_idler)
    return {"mesaj": "Alerjen tercihleriniz güncellendi.", "alerjen_idler": guncel}


@app.get("/api/filtreler")
def filtreler():
    return veri.filtre_secenekleri_getir()


@app.get("/api/menuler")
def menuler(
    response: Response,
    tarih: Optional[str] = Query(None),
    yemekhane_id: Optional[int] = Query(None),
    ogun_id: Optional[int] = Query(None),
    baslangic_tarih: Optional[str] = Query(None),
    bitis_tarih: Optional[str] = Query(None),
    vejetaryen: Optional[bool] = Query(None),
    haric_alerjen: Optional[str] = Query(None),
):
    sonuc, cache_durum = veri.menuleri_getir(
        tarih=tarih,
        yemekhane_id=yemekhane_id,
        ogun_id=ogun_id,
        baslangic_tarih=baslangic_tarih,
        bitis_tarih=bitis_tarih,
        vejetaryen=vejetaryen,
        haric_alerjen=haric_alerjen,
    )
    response.headers["X-Cache"] = cache_durum
    return sonuc


@app.get("/api/menuler/{menu_id}/degerlendirmeler")
def menu_degerlendirmeleri(menu_id: int):
    return veri.menu_degerlendirmeleri_getir(menu_id)


@app.get("/api/rezervasyonlar")
def rezervasyonlarim(kullanici=Depends(mevcut_kullanici)):
    return veri.kullanici_rezervasyonlari_getir(kullanici["id"])


@app.post("/api/rezervasyonlar")
def rezervasyon_yap(istek: RezervasyonIstegi, kullanici=Depends(mevcut_kullanici)):
    porsiyon = (istek.porsiyon or "tam").strip().lower()
    sonuc = veri.rezervasyon_olustur(kullanici["id"], istek.menu_id, porsiyon)
    return {"mesaj": "Rezervasyonunuz oluşturuldu.", **sonuc}


@app.post("/api/rezervasyonlar/{rezervasyon_id}/iptal")
def rezervasyon_iptal(rezervasyon_id: int, kullanici=Depends(mevcut_kullanici)):
    sonuc = veri.rezervasyon_iptal_et(kullanici["id"], rezervasyon_id)
    return {"mesaj": "Rezervasyon iptal edildi ve ücret iade edildi.", **sonuc}


@app.post("/api/bakiye/yukle")
def bakiye_yukle(istek: BakiyeYukleIstegi, kullanici=Depends(mevcut_kullanici)):
    sonuc = veri.bakiye_yukle(kullanici["id"], istek.tutar)
    return {"mesaj": "Bakiye başarıyla yüklendi.", **sonuc}


@app.get("/api/bakiye/hareketler")
def bakiye_hareketleri(kullanici=Depends(mevcut_kullanici)):
    return veri.bakiye_hareketleri_getir(kullanici["id"])


@app.post("/api/degerlendirmeler")
def degerlendirme_ekle(istek: DegerlendirmeIstegi, kullanici=Depends(mevcut_kullanici)):
    if istek.puan < 1 or istek.puan > 5:
        raise HTTPException(status_code=400, detail="Puan 1 ile 5 arasında olmalıdır.")
    yorum = (istek.yorum or "").strip() or None
    if yorum and len(yorum) > 500:
        raise HTTPException(status_code=400, detail="Yorum en fazla 500 karakter olabilir.")
    veri.degerlendirme_ekle(kullanici["id"], istek.menu_id, istek.puan, yorum)
    return {"mesaj": "Değerlendirmeniz kaydedildi."}


@app.get("/api/yonetim/yemekler")
def yonetim_yemekler(_=Depends(yonetici_gerekli)):
    return veri.yonetim_yemekleri_getir()


@app.get("/api/yonetim/kullanicilar")
def yonetim_kullanicilar(_=Depends(yonetici_gerekli)):
    return veri.yonetim_kullanicilari_getir()


@app.post("/api/yonetim/menuler")
def yonetim_menu_ekle(istek: MenuOlusturIstegi, _=Depends(yonetici_gerekli)):
    if istek.ogrenci_fiyati < 0 or istek.personel_fiyati < 0:
        raise HTTPException(status_code=400, detail="Fiyatlar negatif olamaz.")
    yemek_idler = list(dict.fromkeys(istek.yemek_idler))
    if not yemek_idler:
        raise HTTPException(status_code=400, detail="Menü için en az bir yemek seçmelisiniz.")
    menu_id = veri.yonetim_menu_ekle(
        istek.yemekhane_id,
        istek.tarih,
        istek.ogun_id,
        istek.ogrenci_fiyati,
        istek.personel_fiyati,
        yemek_idler,
    )
    return {"mesaj": "Yeni menü eklendi.", "menu_id": menu_id}


@app.put("/api/yonetim/menuler/{menu_id}")
def yonetim_menu_guncelle(menu_id: int, istek: MenuGuncelleIstegi, _=Depends(yonetici_gerekli)):
    if istek.ogrenci_fiyati < 0 or istek.personel_fiyati < 0:
        raise HTTPException(status_code=400, detail="Fiyatlar negatif olamaz.")
    yemek_idler = list(dict.fromkeys(istek.yemek_idler))
    if not yemek_idler:
        raise HTTPException(status_code=400, detail="Menüde en az bir yemek olmalıdır.")
    veri.yonetim_menu_guncelle(
        menu_id,
        istek.ogrenci_fiyati,
        istek.personel_fiyati,
        yemek_idler,
    )
    return {"mesaj": "Menü güncellendi."}


@app.delete("/api/yonetim/menuler/{menu_id}")
def yonetim_menu_sil(menu_id: int, _=Depends(yonetici_gerekli)):
    veri.yonetim_menu_sil(menu_id)
    return {"mesaj": "Menü silindi."}


@app.post("/api/yonetim/turnike")
def yonetim_turnike(istek: TurnikeIstegi, _=Depends(yonetici_gerekli)):
    zaman = (istek.zaman or "").strip() or None
    sonuc = veri.yonetim_turnike_gecis(istek.kullanici_id, istek.yemekhane_id, zaman)
    return {"mesaj": "Turnike geçişi onaylandı.", **sonuc}


@app.get("/api/yonetim/ozet")
def yonetim_ozet(_=Depends(yonetici_gerekli)):
    return veri.yonetim_ozet_getir()