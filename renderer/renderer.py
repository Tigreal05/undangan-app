"""
renderer/renderer.py — Dispatch utama template engine.

render_template(template, wedding_data) -> html final
  - template: dict dengan key minimal {id, render_mode, html_code} (dari DB `templates`)
  - wedding_data: dict data mempelai/acara (lihat placeholder.build_placeholder_map)

load_template(conn, template_id) -> row templates sebagai dict | None
  - client HANYA mengirim template_id; backend yang lookup DB.
  - template_path tidak pernah diterima dari client. Jika html_code kosong dan
    template_path terdaftar (relatif terhadap direktori templates/ server),
    file dibaca dari disk — path divalidasi agar tidak keluar dari templates/.
"""
import os
import sqlite3

from .legacy import render_legacy
from .placeholder import render_placeholder
from .validator import ALLOWED_KEYS, validate_template

PLACEHOLDER_KEYS = ALLOWED_KEYS
REQUIRED_PLACEHOLDERS = ("groom_name", "bride_name", "event_date")

# Root direktori template master sisi server (bukan input client).
TEMPLATES_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")


class RenderError(Exception):
    """Basis error renderer."""


class InvalidTemplateError(RenderError):
    """template_id tidak ditemukan / template tidak aktif / source kosong."""


class UnknownPlaceholderError(RenderError):
    """Template placeholder memakai {{key}} yang tidak ada dalam kontrak."""


def load_template(conn, template_id):
    """Ambil satu template dari DB berdasarkan ID (integer). Return dict atau None.

    Kolom baru (render_mode/template_key/template_path/status) bersifat additive;
    pada database lama yang belum dimigrasi, nilai default dipakai (legacy/active).
    """
    if template_id is None:
        return None
    try:
        tid = int(template_id)
    except (TypeError, ValueError):
        return None
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM templates WHERE id = ?", (tid,))
    except sqlite3.OperationalError:
        # kolom baru belum ada di DB lama — tetap bisa load via kolom lama
        cur.execute(
            "SELECT id, package_id, name, price, discount, duration, image_url, html_code, is_top10 "
            "FROM templates WHERE id = ?", (tid,))
    row = cur.fetchone()
    if not row:
        return None
    t = dict(zip([c[0] for c in cur.description], row))
    t.setdefault("render_mode", "legacy")
    t.setdefault("status", "active")
    t.setdefault("template_path", None)
    return t


def _read_template_source(template):
    """Determinasi source HTML oleh BACKEND (bukan path dari client).

    Prioritas: html_code (source of truth saat ini); jika kosong, template_path
    yang tersimpan di DB (divalidasi harus di bawah TEMPLATES_ROOT).
    """
    html_code = (template.get("html_code") or "").strip()
    if html_code:
        return template.get("html_code") or ""
    tpath = template.get("template_path")
    if tpath:
        # normalisasi & pastikan tidak lolos keluar dari TEMPLATES_ROOT
        candidate = os.path.normpath(os.path.join(TEMPLATES_ROOT, str(tpath).lstrip("/\\")))
        real_root = os.path.realpath(TEMPLATES_ROOT)
        real_cand = os.path.realpath(candidate)
        if (real_cand + os.sep).startswith(real_root + os.sep) or real_cand == real_root:
            if os.path.isfile(real_cand):
                with open(real_cand, "r", encoding="utf-8") as f:
                    return f.read()
    raise InvalidTemplateError("Template id=%s tidak memiliki sumber HTML." % template.get("id"))


def render_template(template, wedding_data):
    """Entrypoint tunggal untuk PREVIEW maupun GENERATION production.

    ONE TEMPLATE + MANY WEDDINGS: hasil render tidak pernah ditulis kembali ke
    template master.
    """
    if not template or not isinstance(template, dict):
        raise InvalidTemplateError("Template tidak valid / tidak ditemukan.")
    if template.get("status") not in (None, "", "active"):
        raise InvalidTemplateError("Template id=%s berstatus %s (tidak dapat dirender)."
                                   % (template.get("id"), template.get("status")))
    mode = (template.get("render_mode") or "legacy").lower()
    source = _read_template_source(template)

    if mode == "legacy":
        # kompatibilitas penuh — tanpa replacement, tanpa validasi placeholder
        return render_legacy(source, wedding_data)

    if mode == "placeholder":
        result = validate_template(source)
        if result["unknown"]:
            raise UnknownPlaceholderError(
                "Template id=%s memakai placeholder tidak dikenal: %s"
                % (template.get("id"), ", ".join(result["unknown"])))
        return render_placeholder(source, wedding_data)

    raise RenderError("render_mode tidak dikenal: %r" % mode)
