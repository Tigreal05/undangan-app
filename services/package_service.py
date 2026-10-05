"""
services/package_service.py — Hirarki paket SILVER / GOLD / PLATINUM VIP.

Positioning (sesuai brief produk):
  🥈 SILVER     : "Undangan digital siap pakai, tinggal isi data." Template-based,
                  layout TIDAK bisa diubah; customer hanya mengisi data + foto + warna.
  🥇 GOLD       : Semua fitur Silver + personalisasi premium (layout/warna/font/cover/
                  background/section/wording, gallery besar, multi video, custom music,
                  custom URL, map preview, multiple event: Akad -> Resepsi ->
                  Ngunduh Mantu / Bride-Groom-Wedding Event), live RSVP, daftar tamu,
                  guestbook, digital envelope, QR code, share WA, OG social preview,
                  mobile optimized.
  👑 PLATINUM   : Bukan "Gold dengan fitur lebih banyak" — layanan FULL CUSTOM:
                  design dari nol, custom layout/animasi/ilustrasi/typography/color
                  system/transition/loading screen/music experience. Client submit
                  request tema (mis. "kerajaan Jawa tapi modern") -> tim buatkan khusus.

Data disimpan ADDITIVE di tabel `settings` (key = 'tier_features_json') sehingga
database lama tetap dapat dibuka tanpa migrasi kolom. Tabel `packages` lama tidak
diubah strukturnya; pemetaan paket DB -> tier memakai kata kunci nama.
"""
import json
import re

SETTING_KEY = "tier_features_json"

TIERS = ("silver", "gold", "platinum")

TIER_EMOJI = {"silver": "🥈", "gold": "🥇", "platinum": "👑"}
TIER_LABEL = {"silver": "SILVER — Simple & Elegant",
              "gold": "GOLD — Personalized & Premium",
              "platinum": "PLATINUM VIP — Fully Custom"}

# Kontrak fitur kanonik (single source of truth untuk UI client & admin).
DEFAULT_FEATURES = {
    "silver": {
        "positioning": "Undangan digital siap pakai, tinggal isi data.",
        "duration": "Aktif 30 hari",
        "customization_level": "template_based",
        "note_layout": "Template-based: isi data, foto, dan warna tertentu. Layout tidak diubah.",
        "info": [
            "Nama mempelai", "Foto mempelai", "Nama orang tua", "Quote/doa",
            "Cerita singkat / couple story", "Tanggal akad", "Tanggal resepsi",
            "Waktu acara", "Nama tempat", "Alamat", "Lokasi di peta", "Countdown",
        ],
        "interaction": [
            "RSVP", "Ucapan & doa", "Tombol WhatsApp", "Navigasi lokasi", "Musik background",
        ],
        "media": ["Cover", "Gallery terbatas (6-10 foto)", "1 video / YouTube"],
        "extras": ["Amplop digital / rekening", "Guest greeting", "Link undangan personal"],
        "limits": ["Template-based, layout tidak dapat diubah", "Custom request: tidak tersedia"],
    },
    "gold": {
        "positioning": "Template premium yang bisa disesuaikan dengan pasangan.",
        "duration": "Aktif 90 hari",
        "customization_level": "personalized_template",
        "note_layout": "Semua fitur Silver + personalisasi template (layout/warna/font/asset).",
        "info": ["Semua fitur Silver"],
        "customization": [
            "Pilihan beberapa layout", "Pilihan warna", "Pilihan font", "Custom cover",
            "Custom background", "Custom section", "Custom wording", "Couple story",
            "Gallery lebih besar", "Video lebih banyak", "Custom music", "Custom URL",
            "Map preview",
            "Multiple event: Akad -> Resepsi -> Ngunduh Mantu",
            "Bride Event / Groom Event / Wedding Event",
        ],
        "premium": [
            "Live RSVP", "Daftar tamu", "Guestbook", "Ucapan", "Digital envelope",
            "QR Code", "Share ke WhatsApp", "Social sharing preview (Open Graph)",
            "Optimized mobile experience",
        ],
        "limits": ["Design dari nol: tidak tersedia (pilih template yang mendekati)"],
    },
    "platinum": {
        "positioning": "Lo punya request? Kita bikinin.",
        "duration": "Aktif 6 bulan",
        "customization_level": "fully_custom",
        "note_layout": "Semua fitur Gold + layanan desain full custom oleh tim.",
        "info": ["Semua fitur Gold"],
        "full_custom": [
            "Design dari nol", "Custom layout", "Custom animation", "Custom illustration",
            "Custom typography", "Custom color system", "Custom transition",
            "Custom loading screen", "Custom music experience",
        ],
        "request_flow": [
            "Client menuliskan request tema pada form pesanan (mis. \"Kerajaan Jawa tapi modern\")",
            "Tim SUKA MOTO membuatkan design khusus sesuai request",
            "Review bersama sebelum undangan dipublikasikan",
        ],
        "comparison": [
            {"need": "Tema custom dari nol (mis. kerajaan Jawa modern)",
             "silver": "x tidak tersedia", "gold": "! pilih template yang mendekati",
             "platinum": "v dibuatkan khusus"},
        ],
    },
}


def normalize_tier(name):
    """'Platinum VIP' / 'Gold Package' / 'silver' -> 'platinum' / 'gold' / 'silver'."""
    s = (name or "").lower()
    if "platinum" in s or "vip" in s:
        return "platinum"
    if "gold" in s:
        return "gold"
    if "silver" in s:
        return "silver"
    return None


def tier_of_package(pkg_row):
    """pkg_row = (id, name, subtitle[, image_url]) -> tier atau None."""
    tier = normalize_tier(pkg_row[1] if len(pkg_row) > 1 else "")
    if not tier and len(pkg_row) > 2:
        tier = normalize_tier(pkg_row[2])
    # fallback positional: 1=silver, 2=gold, 3=platinum (sesuai seed awal)
    if not tier:
        try:
            pid = int(pkg_row[0])
            tier = {1: "silver", 2: "gold", 3: "platinum"}.get(pid)
        except (TypeError, ValueError, IndexError):
            tier = None
    return tier


def load_features(conn):
    """Baca kontrak fitur dari settings; fallback ke DEFAULT_FEATURES (additive)."""
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (SETTING_KEY,))
    row = cur.fetchone()
    if row:
        try:
            data = json.loads(row[0])
            if isinstance(data, dict) and all(t in data for t in TIERS):
                return data
        except (ValueError, TypeError):
            pass
    return json.loads(json.dumps(DEFAULT_FEATURES))


def save_features(conn, features):
    """Simpan kontrak fitur ke settings (server-side only, dipakai admin)."""
    clean = {}
    for t in TIERS:
        if t in features and isinstance(features[t], dict):
            clean[t] = features[t]
    cur = conn.cursor()
    cur.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (SETTING_KEY, json.dumps(clean, ensure_ascii=False)))
    conn.commit()
    return clean


def _esc(value):
    import html as _h
    return _h.escape("" if value is None else str(value), quote=True)


def _list_items(items):
    return "".join('<li><span class="tf-check">&#10003;</span> %s</li>' % _esc(i)
                   for i in items if not str(i).startswith(("x ", "! ", "v ")))


def tier_feature_card_html(tier, feats):
    """Satu kartu rincian fitur untuk halaman /start ([2] Pilih Paket)."""
    f = feats.get(tier) or {}
    sections = ""
    def add(title, items):
        nonlocal sections
        if items:
            sections += ('<div class="tf-group"><div class="tf-title">%s</div>'
                         '<ul class="tf-list">%s</ul></div>') % (_esc(title), _list_items(items))
    add("Informasi", f.get("info"))
    add("Interaksi", f.get("interaction"))
    add("Media", f.get("media"))
    add("Tambahan", f.get("extras"))
    add("Customization", f.get("customization"))
    add("Fitur Premium", f.get("premium"))
    add("Full Custom Design", f.get("full_custom"))
    add("Alur Request Khusus", f.get("request_flow"))
    limits = f.get("limits") or []
    if limits:
        sections += ('<div class="tf-group"><div class="tf-title">Batasannya</div>'
                     '<ul class="tf-list tf-limit">%s</ul></div>') % "".join(
                     '<li><span class="tf-x">&#10005;</span> %s</li>' % _esc(l) for l in limits)
    accent = {"silver": "#9ca3af", "gold": "#fbbf24", "platinum": "#a855f7"}[tier]
    return """
    <details class="tier-feat" style="border-color:%(accent)s33;">
      <summary style="color:%(accent)s;">%(emoji)s Rincian Fitur %(label)s</summary>
      <p class="tf-pos">"%(pos)s"</p>
      <div class="tf-note">%(note)s</div>
      %(sections)s
    </details>""" % dict(accent=accent, emoji=TIER_EMOJI[tier], label=_esc(TIER_LABEL[tier]),
                         pos=_esc(f.get("positioning", "")), note=_esc(f.get("note_layout", "")),
                         sections=sections)


def comparison_table_html(feats):
    """Tabel perbandingan Silver vs Gold vs Platinum (khususnya soal custom request)."""
    rows_cmp = (feats.get("platinum") or {}).get("comparison") or []
    rows = ""
    for c in rows_cmp:
        def cell(v):
            v = str(v or "")
            color = "#ef4444" if v.startswith("x") else ("#fbbf24" if v.startswith("!") else "#34d399")
            icon = "&#10005;" if v.startswith("x") else ("&#9888;" if v.startswith("!") else "&#10004;")
            return '<td style="color:%s;font-size:10px;font-weight:bold;">%s %s</td>' % (
                color, icon, _esc(v[2:] if v[:2] in ("x ", "! ", "v ") else v))
        rows += "<tr><td style='font-size:10px;color:#fff;'>%s</td>%s%s%s</tr>" % (
            _esc(c.get("need", "")), cell(c.get("silver")), cell(c.get("gold")), cell(c.get("platinum")))
    if not rows:
        return ""
    return """
    <div class="order-card" style="border-left:3px solid #a855f7;">
      <h3>Bedanya Apa Sih?</h3>
      <table style="width:100%;border-collapse:collapse;">
        <tr style="border-bottom:1px solid #27272a;">
          <th style="text-align:left;font-size:10px;color:#71717a;padding:4px;">Kebutuhan</th>
          <th style="font-size:10px;color:#9ca3af;padding:4px;">Silver</th>
          <th style="font-size:10px;color:#fbbf24;padding:4px;">Gold</th>
          <th style="font-size:10px;color:#a855f7;padding:4px;">Platinum</th>
        </tr>%s
      </table>
    </div>""" % rows


TIER_CSS = """
.tier-feat { background:#18181b; border:1px solid #27272a; border-radius:10px; margin:-4px 0 14px; padding:0; overflow:hidden; }
.tier-feat summary { cursor:pointer; font-size:11px; font-weight:800; padding:10px 12px; list-style:none; }
.tier-feat summary::-webkit-details-marker { display:none; }
.tf-pos { font-size:11px; color:#f4f4f5; font-style:italic; padding:0 12px 6px; }
.tf-note { font-size:10px; color:#a1a1aa; padding:0 12px 8px; border-bottom:1px dashed #27272a; }
.tf-group { padding:8px 12px; }
.tf-title { font-size:9px; text-transform:uppercase; letter-spacing:1px; color:#fbbf24; font-weight:800; margin-bottom:4px; }
.tf-list { list-style:none; }
.tf-list li { font-size:10.5px; color:#d4d4d8; margin:3px 0; line-height:1.4; }
.tf-check { color:#34d399; margin-right:6px; font-weight:bold; }
.tf-x { color:#ef4444; margin-right:6px; font-weight:bold; }
.tf-limit li { color:#fca5a5; }
.plat-banner { background:linear-gradient(135deg,#3b0764,#1e1b4b); border:1px solid #a855f7; border-radius:12px; padding:14px; margin-bottom:14px; }
.plat-banner h3 { color:#e9d5ff; font-size:13px; margin-bottom:4px; }
.plat-banner p { color:#d8b4fe; font-size:11px; line-height:1.6; }
"""
