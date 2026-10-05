"""
renderer/validator.py — Validasi template placeholder & nilai asset.

Aturan:
  - Placeholder valid hanya pada ALLOWED_KEYS dengan sintaks {{key}} (spasi opsional).
  - Kurung kurawal ganda di CSS/JS yang BUKAN key valid TIDAK dianggap placeholder
    (tidak error, dibiarkan apa adanya oleh renderer).
  - Placeholder tidak dikenal -> error (template ditolak saat disimpan/dipreview).
  - couple_photo harus URL/path asset yang sudah divalidasi server — bukan HTML mentah.
"""
import re

# Kontrak placeholder (section 4 spesifikasi)
ALLOWED_KEYS = (
    "groom_name",
    "bride_name",
    "event_date",
    "event_time",
    "venue",
    "address",
    "couple_photo",
)

# Placeholder yang wajib ada agar undangan informatif (couple_photo boleh opsional:
# beberapa template memakai background CSS, tapi minimal nama & tanggal wajib).
REQUIRED_KEYS = ("groom_name", "bride_name", "event_date")

# Sintaks placeholder: {{ key }} — hanya huruf kecil, digit, underscore.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

# Semua token {{...}} (untuk mendeteksi kandidat placeholder tidak dikenal).
_ANY_TOKEN_RE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


def find_placeholders(html_code):
    """Kembalikan daftar unik key placeholder valid yang dipakai template."""
    seen = []
    for m in _PLACEHOLDER_RE.finditer(html_code or ""):
        key = m.group(1)
        if key in ALLOWED_KEYS and key not in seen:
            seen.append(key)
    return seen


def find_unknown_placeholders(html_code):
    """Token {{...}} yang bentuknya seperti placeholder tapi key-nya tidak dikenal.

    Mengabaikan token yang jelas bukan identifier (mis. `{{ color }}` dari templating
    lain atau JS `{{a:1}}`) — hanya menandai token yang *mirip* key (identifier-ish)
    namun tidak termasuk ALLOWED_KEYS, supaya CSS/JS kurung kurawal tidak memicu error.
    """
    unknown = []
    for m in _ANY_TOKEN_RE.finditer(html_code or ""):
        inner = m.group(1)
        # hanya anggap "kandidat placeholder" bila isinya identifier sederhana
        if not re.fullmatch(r"[A-Za-z0-9_]+", inner):
            continue
        if inner not in ALLOWED_KEYS and inner not in unknown:
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
