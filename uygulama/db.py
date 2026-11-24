import os
from decimal import Decimal
from datetime import date, datetime, timedelta
from typing import Optional
import pymysql
import redis
from fastapi import HTTPException

DB_YAZMA_HOST = os.getenv("DB_YAZMA_HOST", "mysql-primary")
DB_OKUMA_HOST = os.getenv("DB_OKUMA_HOST", "mysql-replica")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_ADI = os.getenv("DB_ADI", "yemekhane")
DB_KULLANICI = os.getenv("DB_KULLANICI", "yemekhane_app")
DB_SIFRE = os.getenv("DB_SIFRE", "Uyg_2026")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def _baglan(host: str):
    return pymysql.connect(
        host=host,
        port=DB_PORT,
        user=DB_KULLANICI,
        password=DB_SIFRE,
        database=DB_ADI,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def db_yazma():
    return _baglan(DB_YAZMA_HOST)


def db_okuma():
    return _baglan(DB_OKUMA_HOST)


def temizle_veri(obj):
    if isinstance(obj, list):
        return [temizle_veri(x) for x in obj]
    if isinstance(obj, dict):
        return {k: temizle_veri(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(obj, date):
        return obj.strftime("%Y-%m-%d")
    if isinstance(obj, timedelta):
        toplam_sn = int(obj.total_seconds())
        saat = toplam_sn // 3600
        dakika = (toplam_sn % 3600) // 60
        return f"{saat:02d}:{dakika:02d}"
    return obj


def sp_cagir(proc_adi: str, params: tuple):
    try:
        with db_yazma() as conn:
            with conn.cursor() as cur:
                cur.callproc(proc_adi, params)
                satir = cur.fetchone()
                while cur.nextset():
                    pass
                return temizle_veri(satir)
    except pymysql.MySQLError as e:
        kod = e.args[0] if e.args else 0
        mesaj = e.args[1] if len(e.args) > 1 else "Veritabanı hatası."
        if kod == 1644:
            raise HTTPException(status_code=400, detail=mesaj)
        raise HTTPException(status_code=500, detail="İşlem sırasında bir hata oluştu.")


def menu_onbellek_temizle(ozel_anahtar: Optional[str] = None) -> int:
    if ozel_anahtar:
        return int(redis_client.delete(ozel_anahtar))
    anahtarlar = redis_client.keys("menu:*")
    if anahtarlar:
        return int(redis_client.delete(*anahtarlar))
    return 0