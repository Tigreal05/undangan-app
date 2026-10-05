"""
services/generation_service.py — GENERATED INVITATION (hasil akhir renderer).

Flow aktivasi (section 18):

    ORDER -> PAYMENT VERIFIED -> WEDDING PUBLISHING -> generate_wedding()
          -> publish -> WEDDING ACTIVE -> (jatuh tempo) EXPIRED

Struktur keluaran:

    generated/weddings/{slug}/
        index.html      <- hasil render_template() (SAME renderer dgn preview)
        assets/         <- salinan asset lokal (foto pasangan, dll.)

PRINSIP: template master tidak pernah disentuh; hasil generate adalah salinan.
"""
import os
import shutil
import sqlite3
import datetime as _dt

from renderer.renderer import load_template, render_template
from services import wedding_service

# Root folder proyek = parent dari direktori services/
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATED_ROOT = os.path.join(BASE_DIR, "generated", "weddings")
UPLOAD_ROOT = os.path.join(BASE_DIR, "static_uploads")


class GenerationError(Exception):
    pass


def _db_conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = None
    return conn


def compute_expires_at(event_date, days):
    """expires_at = tanggal acara + durasi hari (fallback: hari ini + durasi)."""
    try:
        start = _dt.date.fromisoformat((event_date or "").strip())
    except ValueError:
        start = _dt.date.today()
    return (start + _dt.timedelta(days=int(days))).isoformat()


def wedding_output_dir(slug):
    """Path output per slug dengan guard traversal (slug harus sudah server-side-safe)."""
    if not slug or not __import__("re").fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", slug):
        raise GenerationError("Slug tidak aman: %r" % slug)
    path = os.path.normpath(os.path.join(GENERATED_ROOT, slug))
    root = os.path.realpath(GENERATED_ROOT)
    real = os.path.realpath(path)
    if not (real + os.sep).startswith(root + os.sep):
        raise GenerationError("Path output di luar generated root.")
    return path


def copy_asset(cur_url):
    """Salin asset lokal (/static_uploads/...) ke generated/{slug}/assets/.

    Return URL relatif baru pada success, URL asli bila bukan asset lokal.
    Nama file sumber ditentukan SERVER (kolom DB), bukan dari client.
    """
    if not cur_url or not cur_url.startswith("/static_uploads/"):
        return cur_url
    fname = os.path.basename(cur_url.split("?")[0])
    src = os.path.realpath(os.path.join(UPLOAD_ROOT, fname))
    root = os.path.realpath(UPLOAD_ROOT)
    if not (src + os.sep).startswith(root + os.sep) or not os.path.isfile(src):
        return cur_url
    return fname  # caller menyalin setelah dir ada


def generate_wedding(wedding_id, db_path=None, force=False):
    """Generator produksi: wedding -> order -> template -> renderer -> file HTML.

    1. load wedding
    2. load order (template_id dari order — lookup by ID, bukan nama)
    3. load template via renderer.load_template
    4. cek render_mode (dispatch di renderer)
    5. render HTML (renderer yang SAMA dengan preview)
    6. mkdir generated/weddings/{slug}/ (+ assets/)
    7. tulis index.html
    8. salin asset lokal yg dirujuk
    9. return dict {output_dir, index_path, url_assets}
    """
    if db_path is None:
        db_path = os.path.join(BASE_DIR, "undangan.db")
    conn = _db_conn(db_path)
    try:
        wedding = wedding_service.get_wedding(conn, wedding_id)
        if not wedding:
            raise GenerationError("Wedding id=%s tidak ditemukan." % wedding_id)
        order_id = wedding["order_id"]
        cur = conn.cursor()
        cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        row = cur.fetchone()
        if not row:
            raise GenerationError("Order id=%s untuk wedding tidak ditemukan." % order_id)
        order = dict(zip([c[0] for c in cur.description], row))

        # ---- payment gate: hanya VERIFIED yang boleh digenerate (kecuali force utk test/admin) ----
        if not force:
            cur.execute("SELECT state FROM payment_state WHERE order_id = ?", (order_id,))
            ps = cur.fetchone()
            state = ps[0] if ps else order.get("status")
            verified = (state == "VERIFIED") or (order.get("status") in ("verified", "processing", "active"))
            if not verified:
                raise GenerationError(
                    "Payment belum VERIFIED (state=%r); generate ditolak." % state)

        # ---- template by ID dari DB (bukan input client) ----
        template = load_template(conn, order.get("template_id"))
        if not template:
            raise GenerationError("Template id=%r tidak ditemukan." % order.get("template_id"))

        # ---- render memakai RENDERER YANG SAMA dengan preview ----
        wedding_data = wedding_service.to_renderer_data(wedding)
        html_out = render_template(template, wedding_data)

        # ---- tulis hasil ke generated/weddings/{slug}/ ----
        out_dir = wedding_output_dir(wedding["slug"])
        assets_dir = os.path.join(out_dir, "assets")
        os.makedirs(assets_dir, exist_ok=True)
        index_path = os.path.join(out_dir, "index.html")
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(html_out)

        copied = []
        photo = wedding_data.get("couple_photo") or ""
        fname = copy_asset(photo)
        if fname and fname != photo and photo.startswith("/static_uploads/"):
            src = os.path.realpath(os.path.join(UPLOAD_ROOT, fname))
            dst = os.path.join(assets_dir, fname)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
                # rewrite rujukan di index.html agar menunjuk salinan lokal
                rel = "/generated/weddings/%s/assets/%s" % (wedding["slug"], fname)
                with open(index_path, "r", encoding="utf-8") as f:
                    content = f.read()
                content = content.replace(_html_attr_quote(photo), _html_attr_quote(rel))
                with open(index_path, "w", encoding="utf-8") as f:
                    f.write(content)
                copied.append(rel)

        return {
            "wedding_id": wedding["id"],
            "slug": wedding["slug"],
            "output_dir": out_dir,
            "index_path": index_path,
            "assets_copied": copied,
            "render_mode": template.get("render_mode") or "legacy",
        }
    finally:
        conn.close()


def _html_attr_quote(url):
    """Bentuk atribut src/url sebagaimana ditulis renderer (escaped quote=True)."""
    import html as _h
    return _h.escape(url, quote=True)


def publish_wedding(wedding_id, db_path=None, duration_days=30, event_date=None):
    """Aktivasi penuh: PUBLISHING -> generate -> ACTIVE (+ expires_at) .

    Hanya boleh dipanggil setelah payment VERIFIED (dijaga oleh admin action app.py).
    """
    if db_path is None:
        db_path = os.path.join(BASE_DIR, "undangan.db")
    conn = _db_conn(db_path)
    try:
        wedding = wedding_service.get_wedding(conn, wedding_id)
        if not wedding:
            raise GenerationError("Wedding tidak ditemukan.")
        wedding_service.set_status(conn, wedding_id, "PUBLISHING")
        result = generate_wedding(wedding_id, db_path=db_path)
        exp = compute_expires_at(event_date or wedding.get("event_date"), duration_days)
        cur = conn.cursor()
        cur.execute("UPDATE weddings SET status='ACTIVE', expires_at=? WHERE id=?", (exp, wedding_id))
        cur.execute("UPDATE orders SET status='active', expires_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (exp, wedding["order_id"]))
        conn.commit()
        result["expires_at"] = exp
        result["status"] = "ACTIVE"
        return result
    finally:
        conn.close()


def refresh_expired(db_path=None):
    """ACTIVE -> EXPIRED otomatis bila expires_at terlewat."""
    if db_path is None:
        db_path = os.path.join(BASE_DIR, "undangan.db")
    conn = _db_conn(db_path)
    try:
        today = _dt.date.today().isoformat()
        cur = conn.cursor()
        cur.execute("SELECT id FROM weddings WHERE status='ACTIVE' AND expires_at != '' AND expires_at < ?",
                    (today,))
        ids = [r[0] for r in cur.fetchall()]
        for wid in ids:
            cur.execute("UPDATE weddings SET status='EXPIRED' WHERE id=?", (wid,))
        if ids:
            conn.commit()
        return ids
    finally:
        conn.close()
