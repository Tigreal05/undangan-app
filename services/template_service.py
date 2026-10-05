"""
services/template_service.py — Migrasi ADDITIVE + registry helper untuk template engine.

Migrasi (idempoten, tidak menghapus apa pun):
  templates  : + render_mode ('legacy' default), template_key, template_path (NULL ok),
               status ('active' default)
  weddings   : tabel baru (entitas data undangan; order lama tetap utuh)
  payment_state : tabel baru (state pembayaran kanonik flow V1)

Semua kolom lama (html_code, is_top10, price TEXT, dst.) TIDAK diubah/dihapus.
"""
import sqlite3

TEMPLATE_MODES = ("legacy", "placeholder")
TEMPLATE_STATUSES = ("active", "draft", "archived")


def migrate_additive(db_path):
    """Jalankan migrasi additive pada database yang sudah ada. Aman dipanggil berulang."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # ---- templates: tambah kolom baru secara additive ----
    cur.execute("PRAGMA table_info(templates)")
    cols = {c[1] for c in cur.fetchall()}
    if "render_mode" not in cols:
        cur.execute("ALTER TABLE templates ADD COLUMN render_mode TEXT NOT NULL DEFAULT 'legacy'")
    if "template_key" not in cols:
        cur.execute("ALTER TABLE templates ADD COLUMN template_key TEXT")
    if "template_path" not in cols:
        # html_code masih source of truth -> simpan NULL dulu (sesuai spesifikasi)
        cur.execute("ALTER TABLE templates ADD COLUMN template_path TEXT")
    if "status" not in cols:
        cur.execute("ALTER TABLE templates ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")

    # ---- weddings: entitas data undangan (ONE TEMPLATE MANY WEDDINGS) ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS weddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            slug TEXT UNIQUE NOT NULL,             -- server-side, subdomain-safe
            groom_name TEXT NOT NULL DEFAULT '',
            bride_name TEXT NOT NULL DEFAULT '',
            event_date TEXT DEFAULT '',            -- ISO yyyy-mm-dd
            event_time TEXT DEFAULT '',
            venue TEXT DEFAULT '',
            address TEXT DEFAULT '',
            couple_photo TEXT DEFAULT '',          -- path/URL asset hasil validasi server
            message TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'DRAFT',  -- DRAFT | PUBLISHING | ACTIVE | EXPIRED
            expires_at TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ---- payment_state: state machine pembayaran (additive, terpisah dari orders) ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payment_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL UNIQUE,
            state TEXT NOT NULL DEFAULT 'PENDING_PAYMENT',
            amount_snapshot TEXT NOT NULL DEFAULT '',
            note TEXT DEFAULT '',
            paid_at TEXT DEFAULT '',               -- hanya terisi saat VERIFIED
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def ensure_payment_state(conn, order_id, amount_snapshot=""):
    """Buat baris payment_state untuk order legacy bila belum ada (backfill additive)."""
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM payment_state WHERE order_id = ?", (int(order_id),))
    if not cur.fetchone():
        cur.execute("""
            INSERT INTO payment_state (order_id, state, amount_snapshot)
            VALUES (?, 'PENDING_PAYMENT', ?)
        """, (int(order_id), amount_snapshot or ""))
        conn.commit()


def set_template_mode(conn, template_id, render_mode, template_key=None, status=None):
    """Helper admin: atur mode template baru. Validasi sisi server."""
    if render_mode not in TEMPLATE_MODES:
        raise ValueError("render_mode harus salah satu dari %s" % (TEMPLATE_MODES,))
    if status is not None and status not in TEMPLATE_STATUSES:
        raise ValueError("status harus salah satu dari %s" % (TEMPLATE_STATUSES,))
    cur = conn.cursor()
    cur.execute("UPDATE templates SET render_mode = ?, template_key = COALESCE(?, template_key),"
                " status = COALESCE(?, status) WHERE id = ?",
                (render_mode, template_key, status, int(template_id)))
    conn.commit()
