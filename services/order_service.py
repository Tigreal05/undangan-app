"""
services/order_service.py — Order & Payment state (fondasi baru, additive).

State payment kanonik (section 17):

    PENDING_PAYMENT -> PROOF_UPLOADED -> UNDER_REVIEW -> VERIFIED
                                        -> REJECTED -> PROOF_UPLOADED (upload ulang)

Aturan penting:
  - Upload bukti BUKAN pembayaran berhasil. Upload hanya memindahkan
    PENDING_PAYMENT/REJECTED -> PROOF_UPLOADED.
  - HANYA fungsi admin_verify_payment() yang boleh membuat VERIFIED, dan paid_at
    terisi pada saat itu juga.
  - Harga TIDAK PERNAH diambil dari client: order dibuat dengan snapshot harga
    server-side (amount_snapshot) dari tabel templates/packages saat order dibuat.
  - Template dirujuk lewat template_id (integer DB), bukan nama/path.

Tabel `payment_state` ditambahkan ADDITIVE di schema baru; kolom `orders.status`
lama tetap dipakai flow legacy app.py (dua jalur status dipetakan via STATE_MAP).
"""
import re
import time
import secrets
import sqlite3

PAYMENT_STATES = ("PENDING_PAYMENT", "PROOF_UPLOADED", "UNDER_REVIEW", "REJECTED", "VERIFIED")

# Pemetaan state baru <-> status orders lama (kompatibilitas dua arah).
STATE_MAP = {
    "PENDING_PAYMENT":     "pending_payment",
    "PROOF_UPLOADED":      "awaiting_verification",
    "UNDER_REVIEW":        "awaiting_verification",
    "REJECTED":            "rejected_payment",
    "VERIFIED":            "verified",
}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def make_order_code():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "SKT-" + "".join(secrets.choice(alphabet) for _ in range(6))


def create_order(conn, template_id, package_id, couple_name, groom_name, bride_name,
                 event_date, event_time, venue, address, couple_photo="", whatsapp=""):
    """Buat ORDER yang terhubung ke TEMPLATE via ID. Harga = snapshot SERVER.

    Return dict order. Melempar ValueError bila template/package tidak valid.
    Client tidak pernah mengirim harga; amount_snapshot dibaca dari DB.
    """
    cur = conn.cursor()
    try:
        tid = int(template_id)
    except (TypeError, ValueError):
        raise ValueError("template_id tidak valid")
    cur.execute("SELECT id, price FROM templates WHERE id = ?", (tid,))
    tmpl = cur.fetchone()
    if not tmpl:
        raise ValueError("template_id tidak ditemukan di database")
    cur.execute("SELECT id, name FROM packages WHERE id = ?", (package_id,))
    pkg = cur.fetchone()
    if not pkg:
        raise ValueError("package_id tidak ditemukan di database")

    # ---- snapshot harga dari SERVER/DB (bukan input client) ----
    amount_snapshot = tmpl[1] or ""

    while True:
        code = make_order_code()
        if not cur.execute("SELECT 1 FROM orders WHERE code = ?", (code,)).fetchone():
            break

    cur.execute("""
        INSERT INTO orders (code, slug, template_id, package_id, couple_name,
                            groom_name, bride_name, event_date, event_time,
                            venue_name, venue_address, photo_url, whatsapp,
                            status, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_payment', ?)
    """, (code, "", tid, pkg[0], couple_name[:120], groom_name[:120], bride_name[:120],
          event_date[:32], event_time[:64], venue[:200], address[:500],
          couple_photo[:500], re.sub(r"[^0-9+]", "", whatsapp or "")[:20], amount_snapshot))
    oid = cur.lastrowid
    cur.execute("""
        INSERT INTO payment_state (order_id, state, amount_snapshot, created_at, updated_at)
        VALUES (?, 'PENDING_PAYMENT', ?, ?, ?)
    """, (oid, amount_snapshot, _now(), _now()))
    conn.commit()
    return get_order(conn, oid)


def get_order(conn, order_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id = ?", (int(order_id),))
    row = cur.fetchone()
    if not row:
        return None
    o = dict(zip([c[0] for c in cur.description], row))
    cur.execute("SELECT * FROM payment_state WHERE order_id = ?", (o["id"],))
    ps = cur.fetchone()
    o["payment"] = dict(zip([c[0] for c in cur.description], ps)) if ps else None
    return o


def _set_state(cur, order_id, new_state, note=""):
    cur.execute("""
        UPDATE payment_state SET state = ?, note = ?, updated_at = ? WHERE order_id = ?
    """, (new_state, note[:300], _now(), order_id))
    legacy = STATE_MAP.get(new_state)
    if legacy:
        cur.execute("UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (legacy, order_id))


def upload_proof(conn, order_id, proof_filename):
    """Client mengupload bukti bayar -> PROOF_UPLOADED (BUKAN verified!).

    proof_filename adalah nama file hasil penyimpanan sisi SERVER (sudah disanitasi);
    fungsi ini hanya menyimpannya di kolom privat orders.proof_filename — file bukti
    disimpan di luar docroot publik oleh caller (lihat app.py handle_upload_proof_post).
    """
    cur = conn.cursor()
    cur.execute("SELECT order_id, state FROM payment_state WHERE order_id = ?", (int(order_id),))
    row = cur.fetchone()
    if not row:
        raise ValueError("Order tanpa payment state")
    if row[1] == "VERIFIED":
        raise PermissionError("Pembayaran sudah diverifikasi; upload bukti tidak diperlukan.")
    cur.execute("UPDATE orders SET proof_filename = ? WHERE id = ?", (proof_filename[:200], int(order_id)))
    _set_state(cur, int(order_id), "PROOF_UPLOADED", "Bukti pembayaran diupload client")
    conn.commit()
    return get_order(conn, order_id)


def admin_start_review(conn, order_id):
    """Admin membuka review -> UNDER_REVIEW."""
    cur = conn.cursor()
    cur.execute("SELECT state FROM payment_state WHERE order_id = ?", (int(order_id),))
    row = cur.fetchone()
    if not row or row[0] not in ("PROOF_UPLOADED", "UNDER_REVIEW"):
        raise PermissionError("Hanya bukti terupload yang bisa direview.")
    _set_state(cur, int(order_id), "UNDER_REVIEW", "Sedang direview admin")
    conn.commit()
    return get_order(conn, order_id)


def admin_reject_payment(conn, order_id, reason=""):
    """HANYA jalur admin -> REJECTED."""
    cur = conn.cursor()
    cur.execute("SELECT state FROM payment_state WHERE order_id = ?", (int(order_id),))
    row = cur.fetchone()
    if not row or row[0] not in ("PROOF_UPLOADED", "UNDER_REVIEW"):
        raise PermissionError("Tidak dapat menolak pada state %r." % row[0] if row else "Payment state hilang.")
    _set_state(cur, int(order_id), "REJECTED", "Ditolak admin: " + (reason or "-")[:200])
    cur.execute("UPDATE orders SET reject_reason = ? WHERE id = ?", ((reason or "")[:200], int(order_id)))
    conn.commit()
    return get_order(conn, order_id)


def admin_verify_payment(conn, order_id, admin_username="admin"):
    """SATU-SATUNYA jalur menuju VERIFIED (verifikasi admin sungguhan, sisi server).

    paid_at hanya diisi di sini.
    """
    cur = conn.cursor()
    cur.execute("SELECT state FROM payment_state WHERE order_id = ?", (int(order_id),))
    row = cur.fetchone()
    if not row:
        raise ValueError("Order tanpa payment state")
    if row[0] == "VERIFIED":
        return get_order(conn, order_id)  # idempoten
    if row[0] not in ("PROOF_UPLOADED", "UNDER_REVIEW"):
        raise PermissionError("Verifikasi hanya dari PROOF_UPLOADED/UNDER_REVIEW (state sekarang %r)." % row[0])
    cur.execute("""
        UPDATE payment_state SET state='VERIFIED', note=?, paid_at=?, updated_at=? WHERE order_id = ?
    """, ("Diverifikasi oleh admin " + admin_username[:50], _now(), _now(), int(order_id)))
    cur.execute("UPDATE orders SET status = 'verified', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(order_id),))
    conn.commit()
    return get_order(conn, order_id)


def get_payment_state(conn, order_id):
    cur = conn.cursor()
    cur.execute("SELECT state, amount_snapshot, paid_at FROM payment_state WHERE order_id = ?",
                (int(order_id),))
    row = cur.fetchone()
    if not row:
        return None
    return {"state": row[0], "amount_snapshot": row[1], "paid_at": row[2]}
