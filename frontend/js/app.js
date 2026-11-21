let kullanici = null;
let filtreVerisi = null;

// Yardimci fonksiyonlar
const $ = (secici) => document.querySelector(secici);
const $$ = (secici) => document.querySelectorAll(secici);

function bildirimGoster(mesaj, tur = "basari") {
  const el = $("#bildirim");
  el.textContent = mesaj;
  el.className = `bildirim ${tur}`;
  el.classList.remove("gizli");
  clearTimeout(window._bildirimTimer);
  window._bildirimTimer = setTimeout(() => el.classList.add("gizli"), 4500);
}

async function api(yol, secenekler = {}) {
  const ayar = {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...secenekler,
  };
  const yanit = await fetch(yol, ayar);
  const veri = await yanit.json().catch(() => ({}));
  if (!yanit.ok) {
    throw new Error(veri.detail || "İşlem başarısız oldu.");
  }
  return veri;
}

function sekmeAc(sekmeAdi) {
  $$(".sekme").forEach((s) => s.classList.add("gizli"));
  $$(".nav-btn").forEach((b) => b.classList.remove("aktif"));

  const hedef = $(`#sekme-${sekmeAdi}`);
  if (hedef) hedef.classList.remove("gizli");
  const btn = $(`.nav-btn[data-sekme="${sekmeAdi}"]`);
  if (btn) btn.classList.add("aktif");

  if (sekmeAdi === "menuler") menuleriYukle();
  if (sekmeAdi === "rezervasyonlar") rezervasyonlariYukle();
  if (sekmeAdi === "bakiye") bakiyeYukleSayfa();
  if (sekmeAdi === "yonetim") yonetimYukle();
}

function arayuzuGuncelle() {
  const alan = $("#kullanici-alani");
  if (kullanici) {
    $("#nav-rez").classList.remove("gizli");
    $("#nav-bakiye").classList.remove("gizli");
    if (kullanici.rol === "yonetici") {
      $("#nav-yonetim").classList.remove("gizli");
    } else {
      $("#nav-yonetim").classList.add("gizli");
    }

    alan.innerHTML = `
      <span>${kullanici.ad} ${kullanici.soyad} (${kullanici.rol})</span>
      <span class="bakiye-rozet">${Number(kullanici.bakiye).toFixed(2)} TL</span>
      <button class="btn btn-ikincil btn-kucuk" id="btn-cikis">Çıkış</button>
    `;
    $("#btn-cikis").addEventListener("click", cikisYap);
    $("#bakiye-gosterge").textContent = `${Number(kullanici.bakiye).toFixed(2)} TL`;
  } else {
    $("#nav-rez").classList.add("gizli");
    $("#nav-bakiye").classList.add("gizli");
    $("#nav-yonetim").classList.add("gizli");
    alan.innerHTML = `<button class="btn btn-birincil" id="btn-giris-ac">Giriş Yap</button>`;
    $("#btn-giris-ac").addEventListener("click", () => $("#modal-giris").classList.remove("gizli"));
  }
}

async function oturumKontrol() {
  try {
    kullanici = await api("/api/ben");
  } catch {
    kullanici = null;
  }
  arayuzuGuncelle();
}

async function cikisYap() {
  try {
    await api("/api/cikis", { method: "POST" });
    kullanici = null;
    arayuzuGuncelle();
    sekmeAc("menuler");
    bildirimGoster("Çıkış yapıldı.");
  } catch (e) {
    bildirimGoster(e.message, "hata");
  }
}

// Filtreler ve Menuler
async function filtreleriYukle() {
  filtreVerisi = await api("/api/filtreler");

  const tarihSel = $("#f-tarih");
  tarihSel.innerHTML = `<option value="">Tüm Tarihler</option>`;
  filtreVerisi.tarihler.forEach((t) => {
    tarihSel.innerHTML += `<option value="${t}">${t}</option>`;
  });

  const yhSel = $("#f-yemekhane");
  yhSel.innerHTML = `<option value="">Tümü</option>`;
  filtreVerisi.yemekhaneler.forEach((y) => {
    yhSel.innerHTML += `<option value="${y.id}">${y.ad}</option>`;
  });

  const ogunSel = $("#f-ogun");
  ogunSel.innerHTML = `<option value="">Tümü</option>`;
  filtreVerisi.ogunler.forEach((o) => {
    ogunSel.innerHTML += `<option value="${o.id}">${o.ad}</option>`;
  });

  const alerjenKap = $("#f-alerjenler");
  alerjenKap.innerHTML = "";
  filtreVerisi.alerjenler.forEach((a) => {
    alerjenKap.innerHTML += `
      <label class="alerjen-etiket">
        <input type="checkbox" value="${a.id}" class="alerjen-cb" /> ${a.ad}
      </label>
    `;
  });
}

async function menuleriYukle() {
  const params = new URLSearchParams();
  if ($("#f-tarih").value) params.set("tarih", $("#f-tarih").value);
  if ($("#f-yemekhane").value) params.set("yemekhane_id", $("#f-yemekhane").value);
  if ($("#f-ogun").value) params.set("ogun_id", $("#f-ogun").value);
  if ($("#f-vejetaryen").checked) params.set("vejetaryen", "true");

  const haric = [...$$(".alerjen-cb:checked")].map((c) => c.value);
  if (haric.length) params.set("haric_alerjen", haric.join(","));

  const liste = $("#menu-listesi");
  liste.innerHTML = `<p>Menüler yükleniyor...</p>`;

  try {
    const menuler = await api(`/api/menuler?${params.toString()}`);
    if (!menuler.length) {
      liste.innerHTML = `<div class="kart"><p>Seçilen filtrelere uygun menü bulunamadı.</p></div>`;
      return;
    }

    liste.innerHTML = menuler
      .map((m) => {
        const yemekHtml = m.yemekler
          .map(
            (y) => `
            <li>
              <div>
                <strong>${y.yemek}</strong> ${y.vejetaryen ? "🌱" : ""}
                ${y.alerjenler ? `<span class="yemek-alerjen">Alerjen: ${y.alerjenler}</span>` : ""}
              </div>
              <span>${y.kalori} kcal</span>
            </li>
          `
          )
          .join("");

        const puanMetin = m.ortalama_puan
          ? `⭐ ${m.ortalama_puan} (${m.oy_sayisi})`
          : "Henüz puan yok";

        return `
          <div class="menu-kart">
            <div class="menu-ust">
              <div>
                <div class="menu-baslik">${m.yemekhane} · ${m.ogun}</div>
                <div class="menu-alt-bilgi">${m.tarih} · Son: ${m.rezervasyon_son.slice(11, 16)}</div>
              </div>
              <span class="rozet rozet-mavi">${m.toplam_kalori} kcal</span>
            </div>

            <ul class="yemek-listesi">${yemekHtml}</ul>

            <div>
              <div class="menu-alt-bilgi" style="display:flex; justify-content:space-between; margin-bottom:4px;">
                <span>Doluluk: ${m.rezerve}/${m.kapasite} (%${m.doluluk_yuzde})</span>
                <button class="btn btn-ikincil btn-kucuk" onclick="yorumlariAc(${m.menu_id}, '${m.yemekhane} - ${m.ogun} (${m.tarih})')">${puanMetin}</button>
              </div>
              <div class="doluluk-cubuk"><div class="doluluk-ic" style="width:${Math.min(m.doluluk_yuzde, 100)}%"></div></div>
            </div>

            <div class="menu-alt">
              <div class="fiyatlar">
                Öğrenci: <strong>${m.ogrenci_fiyati} TL</strong><br/>
                Personel: <strong>${m.personel_fiyati} TL</strong>
              </div>
              <button class="btn btn-birincil" onclick="rezervasyonYap(${m.menu_id})">Rezervasyon Yap</button>
            </div>
          </div>
        `;
      })
      .join("");
  } catch (e) {
    liste.innerHTML = `<div class="kart"><p>${e.message}</p></div>`;
  }
}

window.rezervasyonYap = async function (menuId) {
  if (!kullanici) {
    $("#modal-giris").classList.remove("gizli");
    bildirimGoster("Rezervasyon yapmak için önce giriş yapmalısınız.", "hata");
    return;
  }
  try {
    const sonuc = await api("/api/rezervasyonlar", {
      method: "POST",
      body: JSON.stringify({ menu_id: menuId }),
    });
    kullanici.bakiye = sonuc.bakiye;
    arayuzuGuncelle();
    bildirimGoster(`${sonuc.mesaj} Kalan bakiye: ${Number(sonuc.bakiye).toFixed(2)} TL`);
    menuleriYukle();
  } catch (e) {
    bildirimGoster(e.message, "hata");
  }
};

window.yorumlariAc = async function (menuId, baslik) {
  $("#yorumlar-baslik").textContent = baslik;
  const kap = $("#yorumlar-icerik");
  kap.innerHTML = "<p>Yükleniyor...</p>";
  $("#modal-yorumlar").classList.remove("gizli");
  try {
    const yorumlar = await api(`/api/menuler/${menuId}/degerlendirmeler`);
    if (!yorumlar.length) {
      kap.innerHTML = "<p>Bu menü için henüz değerlendirme yapılmamış.</p>";
      return;
    }
    kap.innerHTML = yorumlar
      .map(
        (y) => `
        <div class="yorum-oge">
          <div style="display:flex; justify-content:space-between;">
            <strong>${y.kullanici}</strong>
            <span>⭐ ${y.puan}/5</span>
          </div>
          <p style="margin-top:4px;">${y.yorum || "<em>Yorum yok</em>"}</p>
          <small style="color:#64748b;">${y.olusturma}</small>
        </div>
      `
      )
      .join("");
  } catch (e) {
    kap.innerHTML = `<p>${e.message}</p>`;
  }
};

// Rezervasyonlarim
async function rezervasyonlariYukle() {
  const tbody = $("#rez-tablo");
  tbody.innerHTML = `<tr><td colspan="6">Yükleniyor...</td></tr>`;
  try {
    const liste = await api("/api/rezervasyonlar");
    if (!liste.length) {
      tbody.innerHTML = `<tr><td colspan="6">Henüz rezervasyonunuz yok.</td></tr>`;
      return;
    }
    tbody.innerHTML = liste
      .map((r) => {
        const rozetSinif =
          r.durum === "aktif"
            ? "rozet-mavi"
            : r.durum === "kullanildi"
            ? "rozet-yesil"
            : "rozet-gri";

        let islemHtml = "-";
        if (r.durum === "aktif") {
          islemHtml = `<button class="btn btn-tehlike btn-kucuk" onclick="rezervasyonIptal(${r.id})">İptal Et</button>`;
        } else if (r.durum === "kullanildi" && !r.degerlendirme_id) {
          islemHtml = `<button class="btn btn-birincil btn-kucuk" onclick="degerlendirAc(${r.menu_id})">Değerlendir</button>`;
        } else if (r.degerlendirme_id) {
          islemHtml = `<span class="rozet rozet-yesil">⭐ ${r.verilen_puan}/5</span>`;
        }

        return `
          <tr>
            <td>${r.tarih}</td>
            <td>${r.yemekhane}</td>
            <td>${r.ogun}</td>
            <td>${Number(r.ucret).toFixed(2)} TL</td>
            <td><span class="rozet ${rozetSinif}">${r.durum}</span></td>
            <td>${islemHtml}</td>
          </tr>
        `;
      })
      .join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6">${e.message}</td></tr>`;
  }
}

window.rezervasyonIptal = async function (rezId) {
  try {
    const sonuc = await api(`/api/rezervasyonlar/${rezId}/iptal`, { method: "POST" });
    kullanici.bakiye = sonuc.bakiye;
    arayuzuGuncelle();
    bildirimGoster(`${sonuc.mesaj} Yeni bakiye: ${Number(sonuc.bakiye).toFixed(2)} TL`);
    rezervasyonlariYukle();
  } catch (e) {
    bildirimGoster(e.message, "hata");
  }
};

window.degerlendirAc = function (menuId) {
  $("#d-menu-id").value = menuId;
  $("#d-puan").value = "5";
  $("#d-yorum").value = "";
  $("#modal-degerlendir").classList.remove("gizli");
};

// Bakiye
async function bakiyeYukleSayfa() {
  await oturumKontrol();
  const tbody = $("#hareket-tablo");
  tbody.innerHTML = `<tr><td colspan="4">Yükleniyor...</td></tr>`;
  try {
    const hareketler = await api("/api/bakiye/hareketler");
    if (!hareketler.length) {
      tbody.innerHTML = `<tr><td colspan="4">Hareket kaydı yok.</td></tr>`;
      return;
    }
    tbody.innerHTML = hareketler
      .map(
        (h) => `
        <tr>
          <td>${h.olusturma}</td>
          <td>${h.tur}</td>
          <td style="font-weight:600; color:${h.tutar >= 0 ? "#166534" : "#991b1b"}">
            ${h.tutar >= 0 ? "+" : ""}${Number(h.tutar).toFixed(2)} TL
          </td>
          <td>${h.aciklama || "-"}</td>
        </tr>
      `
      )
      .join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="4">${e.message}</td></tr>`;
  }
}

// Yonetim Paneli
async function yonetimYukle() {
  try {
    const [ozet, kullanicilar, yemekler] = await Promise.all([
      api("/api/yonetim/ozet"),
      api("/api/yonetim/kullanicilar"),
      api("/api/yonetim/yemekler"),
    ]);

    const g = ozet.genel;
    $("#yonetim-ozet-kartlar").innerHTML = `
      <div class="ozet-kutu"><span>Toplam Kullanıcı</span><strong>${g.toplam_kullanici}</strong></div>
      <div class="ozet-kutu"><span>Öğrenci</span><strong>${g.ogrenci_sayisi}</strong></div>
      <div class="ozet-kutu"><span>Personel</span><strong>${g.personel_sayisi}</strong></div>
      <div class="ozet-kutu"><span>Toplam Bakiye</span><strong>${Number(g.toplam_bakiye).toFixed(2)} TL</strong></div>
    `;

    $("#t-kullanici").innerHTML = kullanicilar
      .map((k) => `<option value="${k.id}">${k.ad} ${k.soyad} (${k.rol} · ID:${k.id})</option>`)
      .join("");

    const yhSecenek = filtreVerisi.yemekhaneler
      .map((y) => `<option value="${y.id}">${y.ad}</option>`)
      .join("");
    $("#t-yemekhane").innerHTML = yhSecenek;
    $("#m-yemekhane").innerHTML = yhSecenek;

    $("#m-ogun").innerHTML = filtreVerisi.ogunler
      .map((o) => `<option value="${o.id}">${o.ad}</option>`)
      .join("");

    $("#m-yemekler").innerHTML = yemekler
      .map(
        (y) => `
        <label>
          <input type="checkbox" value="${y.id}" class="m-yemek-cb" />
          ${y.ad} (${y.kalori} kcal)
        </label>
      `
      )
      .join("");

    $("#yonetim-doluluk-tablo").innerHTML = ozet.doluluklar
      .map(
        (d) => `
        <tr>
          <td>${d.tarih}</td>
          <td>${d.yemekhane}</td>
          <td>${d.ogun}</td>
          <td>${d.rezerve}/${d.kapasite} (%${d.doluluk_yuzde})</td>
          <td>${d.ortalama_puan ? `⭐ ${d.ortalama_puan} (${d.oy_sayisi})` : "-"}</td>
          <td><button class="btn btn-tehlike btn-kucuk" onclick="menuSil(${d.menu_id})">Sil</button></td>
        </tr>
      `
      )
      .join("");
  } catch (e) {
    bildirimGoster(e.message, "hata");
  }
}

window.menuSil = async function (menuId) {
  if (!confirm("Bu menüyü silmek istediğinize emin misiniz?")) return;
  try {
    const sonuc = await api(`/api/yonetim/menuler/${menuId}`, { method: "DELETE" });
    bildirimGoster(sonuc.mesaj);
    yonetimYukle();
  } catch (e) {
    bildirimGoster(e.message, "hata");
  }
};

// Olay dinleyicileri
document.addEventListener("DOMContentLoaded", async () => {
  $$(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => sekmeAc(btn.dataset.sekme));
  });

  $$(".modal-kapat").forEach((btn) => {
    btn.addEventListener("click", () => btn.closest(".modal").classList.add("gizli"));
  });

  $("#btn-filtrele").addEventListener("click", menuleriYukle);
  $("#btn-filtre-temizle").addEventListener("click", () => {
    $("#f-tarih").value = "";
    $("#f-yemekhane").value = "";
    $("#f-ogun").value = "";
    $("#f-vejetaryen").checked = false;
    $$(".alerjen-cb").forEach((c) => (c.checked = false));
    menuleriYukle();
  });

  $("#form-giris").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const sonuc = await api("/api/giris", {
        method: "POST",
        body: JSON.stringify({
          eposta: $("#g-eposta").value,
          sifre: $("#g-sifre").value,
        }),
      });
      kullanici = sonuc.kullanici;
      $("#modal-giris").classList.add("gizli");
      $("#form-giris").reset();
      arayuzuGuncelle();
      bildirimGoster(`Hoş geldiniz, ${kullanici.ad}!`);
    } catch (err) {
      bildirimGoster(err.message, "hata");
    }
  });

  $$(".hizli-tutar").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("#bakiye-tutar").value = btn.dataset.tutar;
    });
  });

  $("#form-bakiye").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const tutar = Number($("#bakiye-tutar").value);
      const sonuc = await api("/api/bakiye/yukle", {
        method: "POST",
        body: JSON.stringify({ tutar }),
      });
      kullanici.bakiye = sonuc.bakiye;
      $("#bakiye-tutar").value = "";
      arayuzuGuncelle();
      bildirimGoster(`${sonuc.mesaj} Yeni bakiye: ${Number(sonuc.bakiye).toFixed(2)} TL`);
      bakiyeYukleSayfa();
    } catch (err) {
      bildirimGoster(err.message, "hata");
    }
  });

  $("#form-degerlendir").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const sonuc = await api("/api/degerlendirmeler", {
        method: "POST",
        body: JSON.stringify({
          menu_id: Number($("#d-menu-id").value),
          puan: Number($("#d-puan").value),
          yorum: $("#d-yorum").value,
        }),
      });
      $("#modal-degerlendir").classList.add("gizli");
      bildirimGoster(sonuc.mesaj);
      rezervasyonlariYukle();
    } catch (err) {
      bildirimGoster(err.message, "hata");
    }
  });

  $("#form-turnike").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const sonuc = await api("/api/yonetim/turnike", {
        method: "POST",
        body: JSON.stringify({
          kullanici_id: Number($("#t-kullanici").value),
          yemekhane_id: Number($("#t-yemekhane").value),
          zaman: $("#t-zaman").value || null,
        }),
      });
      bildirimGoster(`${sonuc.mesaj} (${sonuc.ogun})`);
    } catch (err) {
      bildirimGoster(err.message, "hata");
    }
  });

  $("#form-menu-ekle").addEventListener("submit", async (e) => {
    e.preventDefault();
    const yemekIdler = [...$$(".m-yemek-cb:checked")].map((c) => Number(c.value));
    try {
      const sonuc = await api("/api/yonetim/menuler", {
        method: "POST",
        body: JSON.stringify({
          yemekhane_id: Number($("#m-yemekhane").value),
          tarih: $("#m-tarih").value,
          ogun_id: Number($("#m-ogun").value),
          ogrenci_fiyati: Number($("#m-ogr-fiyat").value),
          personel_fiyati: Number($("#m-per-fiyat").value),
          yemek_idler: yemekIdler,
        }),
      });
      bildirimGoster(sonuc.mesaj);
      $("#form-menu-ekle").reset();
      yonetimYukle();
    } catch (err) {
      bildirimGoster(err.message, "hata");
    }
  });

  await filtreleriYukle();
  await oturumKontrol();
  await menuleriYukle();
});