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

V2 — HIRARKI PAKET (SILVER / GOLD / PLATINUM VIP), ADDITIVE:
  - Placeholder inti (ALLOWED_KEYS) selalu diisi di semua tier.
  - Placeholder EXTRA per-tier (validator.SILVER/GOLD/PLATINUM_EXTRA_KEYS) ikut
    diisi bila aktif pada tier order (data["_tier"]); token extra dari tier yang
    lebih tinggi di-reset "" sehingga master template tunggal tetap valid lintas
    paket tanpa meninggalkan {{...}} di halaman produksi.
  - Key URL (gallery/maps/video/music/dsb.) divalidasi via validate_photo_value;
    key warna (theme_accent) disanitasi anti CSS-injection; key font disanitasi;
    key tanggal ISO (countdown_target dsb.) disanitasi ke format ketat.
"""
import html as _html
import re

from .validator import (ALLOWED_KEYS, validate_photo_value, all_known_keys,
                        extra_keys_for_tier)

# Token placeholder: {{ key }} dengan spasi opsional.
_TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

# Fallback gambar ketika couple_photo tidak ada / tidak valid (aman, tanpa JS).
DEFAULT_COUPLE_PHOTO = "/static_uploads/default-couple.svg"

# ---- Kelompok key tambahan per tipe nilai (V2) ------------------------------
URL_KEYS = (
    "couple_photo", "maps_url", "maps_embed_url", "cover_image", "bg_image",
    "gallery_1", "gallery_2", "gallery_3", "gallery_4", "gallery_5", "gallery_6",
    "gallery_7", "gallery_8", "gallery_9", "gallery_10",
    "video_url", "video_2_url", "video_3_url", "music_url",
    "rsvp_url", "wa_link", "invite_url", "og_image", "map_preview",
    "qrcode_url", "custom_reference",
)
COLOR_KEYS = ("theme_accent", "theme_accent_soft")
FONT_KEYS = ("font_heading", "font_body")
ISO_DATE_KEYS = ("countdown_target", "wedding_start_iso", "ngunduh_mantu_date",
                 "akkad_date", "reception_date")

_DEFAULTS = {
    "event_time": "-", "venue": "-", "address": "-", "date_id": "-",
    "theme_accent": "#fbbf24", "theme_accent_soft": "#fef3c7",
    "guestbook_enabled": "false",
}


def _escape_text(value):
    """Escape nilai teks client sebelum masuk HTML."""
    return _html.escape("" if value is None else str(value), quote=True)


_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,19}$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?$")


def _safe_color(value):
    """Hanya #hex atau nama warna sederhana — cegah CSS injection."""
    v = ("" if value is None else str(value)).strip()
    if _HEX_RE.match(v) or _NAME_RE.match(v):
        return v
    return ""


def _safe_font(value):
    v = ("" if value is None else str(value)).strip()
    if v and len(v) <= 40 and all(ch.isalnum() or ch in " -,'" for ch in v):
        return v
    return ""


def _safe_iso(value):
    """Sanitasi nilai tanggal/waktu untuk dipakai langsung oleh JS countdown."""
    v = ("" if value is None else str(value)).strip()
    if _ISO_RE.match(v):
        return v.replace(" ", "T")
    if re.fullmatch(r"\d{1,13}", v):   # epoch detik/milis
        return v
    return ""


def build_placeholder_map(wedding_data):
    """Susun PLACEHOLDER_MAP: key -> string SIAP PAKAI (sudah escaped/validasi).

    wedding_data memuat key inti (groom_name, bride_name, event_date, event_time,
    venue, address, couple_photo) + key extra hirarki paket + "_tier"
    (silver/gold/platinum) yang menentukan kelompok extra mana yang aktif.
    """
    data = wedding_data or {}
    tier = data.get("_tier") or "silver"
    active_extra = set(extra_keys_for_tier(tier))
    known = all_known_keys()
    mapping = {}
    for key in known:
        is_core = key in ALLOWED_KEYS
        if not is_core and key not in active_extra:
            # Extra milik tier lebih tinggi: reset kosong, bukan biarkan {{...}}.
            mapping[key] = ""
            continue
        raw = data.get(key, "")
        if key == "couple_photo":
            if isinstance(raw, str) and validate_photo_value(raw):
                mapping[key] = _html.escape(raw.strip(), quote=True)
            else:
                mapping[key] = DEFAULT_COUPLE_PHOTO
        elif key in URL_KEYS:
            v = ("" if raw is None else str(raw)).strip()
            mapping[key] = _html.escape(v, quote=True) if (not v or validate_photo_value(v)) else ""
        elif key in COLOR_KEYS:
            mapping[key] = _safe_color(raw) or _escape_text(_DEFAULTS.get(key, ""))
        elif key in FONT_KEYS:
            mapping[key] = _escape_text(_safe_font(raw))
        elif key in ISO_DATE_KEYS:
            mapping[key] = _safe_iso(raw)
        else:
            val = raw
            if val in (None, "") and key in _DEFAULTS:
                val = _DEFAULTS[key]
            mapping[key] = _escape_text(val)
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
