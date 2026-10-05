"""
renderer/validator.py — Validasi template placeholder & nilai asset.

Aturan:
  - Placeholder valid hanya pada ALLOWED_KEYS dengan sintaks {{key}} (spasi opsional).
  - Kurung kurawal ganda di CSS/JS yang BUKAN key valid TIDAK dianggap placeholder
    (tidak error, dibiarkan apa adanya oleh renderer).
  - Placeholder tidak dikenal -> error (template ditolak saat disimpan/dipreview).
  - Placeholder EXTRA (opsional) baru aktif sesuai tier paket order (Silver/Gold/Platinum);
    token {{...}} dari kelompok tier yang TIDAK aktif tetap lolos validasi (direset ""),
    sehingga satu master template bisa dipakai lintas paket tanpa error.
  - couple_photo harus URL/path asset yang sudah divalidasi server — bukan HTML mentah.
"""
import re

# Kontrak placeholder inti (section 4 spesifikasi) — selalu aktif di semua tier.
ALLOWED_KEYS = (
    "groom_name",
    "bride_name",
    "event_date",
    "event_time",
    "venue",
    "address",
    "couple_photo",
)

# ---- Placeholder hirarki paket (ADDITIVE V2) -------------------------------
# Silver: siap pakai, tinggal isi data (layout tidak berubah).
SILVER_EXTRA_KEYS = (
    "groom_full_name", "bride_full_name",        # nama lengkap + gelar
    "groom_parents", "bride_parents",            # nama orang tua
    "quote_promise",                             # quote/doa
    "couple_story",                              # cerita singkat / couple story
    "akkad_date", "akkad_time",                  # tanggal akad
    "reception_date", "reception_time",          # tanggal resepsi
    "venue_name", "venue_address",               # nama tempat & alamat
    "maps_url", "maps_embed_url",                # lokasi di peta
    "countdown_target",                          # countdown (ISO datetime)
    "theme_accent", "theme_accent_soft",         # warna tertentu (CSS color)
    "cover_image",                               # cover
    "gallery_1", "gallery_2", "gallery_3", "gallery_4",
    "gallery_5", "gallery_6",                    # gallery terbatas 6-10 (inti 6)
    "video_url",                                 # 1 video / YouTube
    "music_url",                                 # musik background
    "rsvp_url", "wa_link",                       # RSVP & tombol WhatsApp
    "gift_bank", "gift_account_number", "gift_account_name",  # amplop digital
    "to_guest",                                  # guest greeting / link personal
    "invite_url",                                # link undangan personal
    "date_id",                                   # tanggal format Indonesia
    "wedding_start_iso",                         # internal: epoch detik start acara
)
# Gold: semua Silver + personalisasi & multiple event.
GOLD_EXTRA_KEYS = (
    "font_heading", "font_body",                 # pilihan font
    "bg_image",                                  # custom background
    "wording_open",                              # custom wording
    "gallery_7", "gallery_8", "gallery_9", "gallery_10",  # gallery lebih besar (s.d. 10)
    "video_2_url", "video_3_url",                # video lebih banyak
    "ngunduh_mantu_date", "ngunduh_mantu_detail",  # Akad -> Resepsi -> Ngunduh Mantu
    "bride_event", "groom_event", "wedding_event",   # Bride/Groom/Wedding Event
    "custom_slug",                               # custom URL
    "map_preview",                               # map preview (embed)
    "og_title", "og_description", "og_image",    # social sharing preview
    "guestbook_enabled", "qrcode_url",           # guestbook & QR code
)
# Platinum: semua Gold + penanda layanan full custom.
PLATINUM_EXTRA_KEYS = (
    "custom_request",                            # request tema dari client (mis. kerajaan Jawa modern)
    "custom_reference",                          # link moodboard/referensi
    "design_note",                               # catatan tim: dibuatkan khusus dari nol
)

EXTRA_KEY_GROUPS = {
    "silver": SILVER_EXTRA_KEYS,
    "gold": GOLD_EXTRA_KEYS,
    "platinum": PLATINUM_EXTRA_KEYS,
}

def extra_keys_for_tier(tier):
    """Placeholder opsional yang aktif untuk tier tertentu (Gold mencakup Silver, dst.)."""
    keys = []
    for t in ("silver", "gold", "platinum"):
        keys.extend(EXTRA_KEY_GROUPS[t])
        if t == (tier or "silver"):
            break
    return tuple(keys)

# Placeholder yang wajib ada agar undangan informatif (couple_photo boleh opsional:
# beberapa template memakai background CSS, tapi minimal nama & tanggal wajib).
REQUIRED_KEYS = ("groom_name", "bride_name", "event_date")

# Sintaks placeholder: {{ key }} — hanya huruf kecil, digit, underscore.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

# Semua token {{...}} (untuk mendeteksi kandidat placeholder tidak dikenal).
_ANY_TOKEN_RE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


def all_known_keys(tier=None):
    """Seluruh key yang dikenal validator (inti + seluruh extra lintas tier)."""
    keys = set(ALLOWED_KEYS)
    for group in EXTRA_KEY_GROUPS.values():
        keys.update(group)
    return keys


def find_placeholders(html_code):
    """Kembalikan daftar unik key placeholder valid yang dipakai template."""
    known = all_known_keys()
    seen = []
    for m in _PLACEHOLDER_RE.finditer(html_code or ""):
        key = m.group(1)
        if key in known and key not in seen:
            seen.append(key)
    return seen


def find_unknown_placeholders(html_code):
    """Token {{...}} yang bentuknya seperti placeholder tapi key-nya tidak dikenal."""
    known = all_known_keys()
    unknown = []
    for m in _ANY_TOKEN_RE.finditer(html_code or ""):
        inner = m.group(1)
        # hanya anggap "kandidat placeholder" bila isinya identifier sederhana
        if not re.fullmatch(r"[A-Za-z0-9_]+", inner):
            continue
        if inner not in known and inner not in unknown:
            unknown.append(inner)
    return unknown


def validate_photo_value(value):
    """couple_photo harus berupa path/URL asset yang aman, bukan HTML mentah.

    Diizinkan:
      - path relatif lokal hasil upload server: "/static_uploads/<nama>.jpg"
      - URL http(s) eksternal (CDN gambar template)
    Ditolak:
      - string kosong/bukan str
      - mengandung karakter HTML (< > " ' dsb), scheme javascript:, data:text/html
    """
    if not isinstance(value, str) or not value.strip():
        return False
    v = value.strip()
    # tidak boleh ada karakter yang bisa membentuk tag/attr
    if re.search(r"[<>\"'`\x00]", v):
        return False
    low = v.lower()
    if low.startswith("javascript:") or low.startswith("vbscript:"):
        return False
    if low.startswith("data:"):
        # hanya image data-URI yang boleh; text/html data URI ditolak
        if not low.startswith(("data:image/jpeg", "data:image/png", "data:image/webp", "data:image/gif")):
            return False
        return True
    if v.startswith("/"):
        # path lokal: hanya /static_uploads/ atau /generated-assets/ yang dihasilkan server
        if not re.match(r"^/(static_uploads|generated)/[\w\-./%]+$", v):
            return False
        return True
    if low.startswith("http://") or low.startswith("https://"):
        return True
    return False


def validate_template(html_code):
    """Validasi source template mode placeholder.

    Return terstruktur:
      {
        "valid": bool,
        "placeholders": [key, ...],       # placeholder valid yang dipakai
        "unknown": [token, ...],          # placeholder tidak dikenal (error)
        "missing_required": [key, ...],   # placeholder wajib yang tidak ada
        "errors": [str, ...],
        "warnings": [str, ...],
      }
    """
    errors, warnings = [], []
    if not html_code or not html_code.strip():
        return {
            "valid": False,
            "placeholders": [],
            "unknown": [],
            "missing_required": list(REQUIRED_KEYS),
            "errors": ["html_code kosong"],
            "warnings": [],
        }
    placeholders = find_placeholders(html_code)
    unknown = find_unknown_placeholders(html_code)
    missing_required = [k for k in REQUIRED_KEYS if k not in placeholders]

    if unknown:
        errors.append("Placeholder tidak dikenal: " + ", ".join("{{%s}}" % u for u in unknown))
    if missing_required:
        errors.append("Placeholder wajib belum dipakai: " + ", ".join("{{%s}}" % k for k in missing_required))
    if not unknown and not placeholders:
        warnings.append("Template placeholder tidak memakai placeholder sama sekali.")
    if "couple_photo" not in placeholders:
        warnings.append("Placeholder {{couple_photo}} tidak dipakai (foto pasangan tidak akan tampil).")

    return {
        "valid": len(errors) == 0,
        "placeholders": placeholders,
        "unknown": unknown,
        "missing_required": missing_required,
        "errors": errors,
        "warnings": warnings,
    }
