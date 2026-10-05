"""
renderer/placeholder.py — Renderer untuk render_mode == "placeholder".

PENTING (sesuai spesifikasi):
  - TIDAK memakai f-string / str.format() untuk replacement, karena template HTML
    boleh mengandung kurung kurawal pada CSS/JavaScript.
  - Replacement dilakukan eksplisit per key: token "{{groom_name}}" -> nilai escaped.
  - Semua nilai TEXT di-HTML-escape (html.escape(quote=True)) agar client tidak bisa
    menyuntikkan HTML/JS.
  - {{couple_photo}} BUKAN text bebas: nilainya harus URL/path asset yang sudah
    divalidasi server (validator.validate_photo_value). Nilai tidak valid diganti
    placeholder gambar aman.
  - Token {{...}} dengan key yang TIDAK dikenal dibiarkan apa adanya (bisa jadi
    bagian CSS/JS) — deteksi error dilakukan validator saat template disimpan.
"""
import html as _html
import re

from .validator import ALLOWED_KEYS, validate_photo_value

# Token placeholder: {{ key }} dengan spasi opsional.
_TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

# Fallback gambar ketika couple_photo tidak ada / tidak valid (aman, tanpa JS).
DEFAULT_COUPLE_PHOTO = "/static_uploads/default-couple.svg"


def _escape_text(value):
    """Escape nilai teks client sebelum masuk HTML."""
    return _html.escape("" if value is None else str(value), quote=True)


def build_placeholder_map(wedding_data):
    """Susun PLACEHOLDER_MAP: key -> string SIAP PAKAI (sudah escaped/validasi).

    wedding_data adalah dict minimal berisi:
      groom_name, bride_name, event_date, event_time, venue, address, couple_photo
    Key tambahan diabaikan oleh renderer (hanya ALLOWED_KEYS yang dipakai).
    """
    data = wedding_data or {}
    mapping = {}
    for key in ALLOWED_KEYS:
        raw = data.get(key, "")
        if key == "couple_photo":
            # bukan text biasa — harus URL/path asset tervalidasi
            if isinstance(raw, str) and validate_photo_value(raw):
                mapping[key] = _html.escape(raw.strip(), quote=True)
            else:
                mapping[key] = DEFAULT_COUPLE_PHOTO
        else:
            mapping[key] = _escape_text(raw)
    return mapping


def render_placeholder(html_code, wedding_data):
    """Render template placeholder dengan data wedding.

    Return HTML final. Implementasi ONE TEMPLATE + MANY WEDDINGS:
    source html_code tidak pernah diubah; hasil render adalah salinan baru.
    """
    source = html_code or ""
    mapping = build_placeholder_map(wedding_data)

    def repl(m):
        key = m.group(1)
        if key in mapping:          # hanya key kontrak yang diganti
            return mapping[key]
        return m.group(0)           # {{something}} lain (CSS/JS) dibiarkan utuh

    return _TOKEN_RE.sub(repl, source)
