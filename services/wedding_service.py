"""
services/wedding_service.py — Entitas WEDDING (data undangan).

Hubungan: PACKAGE -> TEMPLATE -> ORDER -> WEDDING.
Wedding terhubung ke order via weddings.order_id; wedding menyimpan slug server-side
dan status lifecycle:

    DRAFT -> PUBLISHING -> ACTIVE -> EXPIRED

Nomor WhatsApp client TIDAK dimasukkan ke template sebagai default; ia disimpan di
orders (kontak/order information) dan tidak diekspos ke wedding_data renderer.

Semua fungsi menerima koneksi sqlite3 yang sudah dibuka (caller yang commit/close),
agar mudah dipakai dari app.py maupun test. Semua SQL memakai parameter binding.
"""
import re
import datetime as _dt

WEDDING_STATUSES = ("DRAFT", "PUBLISHING", "ACTIVE", "EXPIRED")

# Kolom yang boleh diisi dari data formulir (whitelist — input lain diabaikan).
_EDITABLE_FIELDS = (
    "slug", "groom_name", "bride_name", "event_date", "event_time",
    "venue", "address", "couple_photo", "message",
)


def _now_iso():
    return _dt.datetime.now().isoformat(timespec="seconds")


def slugify(value):
    """Slug SERVER-SIDE: 'Ahmad & Aisyah' -> 'ahmad-aisyah'.

    Aman untuk subdomain: lowercase, hanya [a-z0-9-], maks 48 char.
    """
    s = (value or "").lower()
    s = s.replace("&", " dan ")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:48]


def unique_slug(cur, base, conn_table="weddings"):
    """Pastikan slug unik; bila terpakai -> base-2, base-3, dst."""
    if not base:
        base = "undangan"
    slug, n = base, 1
    while cur.execute(
            "SELECT 1 FROM %s WHERE slug = ? AND status != 'DELETED'" % conn_table, (slug,)).fetchone():
        n += 1
        slug = "%s-%d" % (base, n)
    return slug


def create_wedding(conn, order_id, groom_name, bride_name, **fields):
    """Buat wedding DRAFT yang terhubung ke order. Return dict wedding.

    Slug selalu dihitung server-side dari nama pasangan (bukan dari client),
    lalu dibuat unik terhadap tabel weddings.
    couple_photo harus path/URL hasil validasi server (lihat validator).
    """
    cur = conn.cursor()
    try:
        oid = int(order_id)
    except (TypeError, ValueError):
        raise ValueError("order_id harus integer, diterima: %r" % (order_id,))
    base = slugify("%s %s" % (groom_name or "", bride_name or ""))
    slug = unique_slug(cur, base)
    data = {
        "order_id": oid,
        "slug": slug,
        "groom_name": (groom_name or "")[:120],
        "bride_name": (bride_name or "")[:120],
        "event_date": (fields.get("event_date") or "")[:32],
        "event_time": (fields.get("event_time") or "")[:64],
        "venue": (fields.get("venue") or "")[:200],
        "address": (fields.get("address") or "")[:500],
        "couple_photo": (fields.get("couple_photo") or "")[:500],
        "message": (fields.get("message") or "")[:1000],
        "status": "DRAFT",
        "expires_at": fields.get("expires_at") or "",
        "created_at": _now_iso(),
    }
    cur.execute("""
        INSERT INTO weddings (order_id, slug, groom_name, bride_name, event_date, event_time,
                              venue, address, couple_photo, message, status, expires_at, created_at)
        VALUES (:order_id, :slug, :groom_name, :bride_name, :event_date, :event_time,
                :venue, :address, :couple_photo, :message, :status, :expires_at, :created_at)
    """, data)
    conn.commit()
    return get_wedding(conn, cur.lastrowid)


def get_wedding(conn, wedding_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM weddings WHERE id = ?", (int(wedding_id),))
    row = cur.fetchone()
    if not row:
        return None
    return dict(zip([c[0] for c in cur.description], row))


def get_wedding_by_order(conn, order_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM weddings WHERE order_id = ? ORDER BY id DESC LIMIT 1", (int(order_id),))
    row = cur.fetchone()
    if not row:
        return None
    return dict(zip([c[0] for c in cur.description], row))


def update_wedding(conn, wedding_id, **fields):
    """Update kolom whitelisted saja. Status TIDAK diubah lewat fungsi ini."""
    cur = conn.cursor()
    sets, vals = [], []
    for key in _EDITABLE_FIELDS:
        if key in fields:
            sets.append("%s = ?" % key)
            vals.append(str(fields[key] or "")[:500])
    if not sets:
        return get_wedding(conn, wedding_id)
    vals.append(int(wedding_id))
    cur.execute("UPDATE weddings SET %s WHERE id = ?" % ", ".join(sets), vals)
    conn.commit()
    return get_wedding(conn, wedding_id)


def set_status(conn, wedding_id, status):
    """Transisi status wedding. Hanya status kanonik yang diterima.

    Aturan aktivasi (section 18): PUBLISHING/ACTIVE hanya dipanggil dari jalur
    payment VERIFIED (generation_service / admin action), bukan dari client.
    """
    if status not in WEDDING_STATUSES:
        raise ValueError("Status wedding tidak valid: %r" % status)
    cur = conn.cursor()
    cur.execute("UPDATE weddings SET status = ? WHERE id = ?", (status, int(wedding_id)))
    conn.commit()
    return get_wedding(conn, wedding_id)


def to_renderer_data(wedding):
    """Susun wedding_data untuk renderer (KONTRAK PLACEHOLDER).

    Sengaja TIDAK menyertakan nomor WhatsApp / info order — kontak WA hanya untuk
    kebutuhan operasional, bukan konten template.
    """
    return {
        "groom_name": wedding.get("groom_name", ""),
        "bride_name": wedding.get("bride_name", ""),
        "event_date": wedding.get("event_date", ""),
        "event_time": wedding.get("event_time", ""),
        "venue": wedding.get("venue", ""),
        "address": wedding.get("address", ""),
        "couple_photo": wedding.get("couple_photo", ""),
    }
