import os
import re
import html as html_lib
import sqlite3
import hashlib
import hmac
import secrets
import base64
import time
import mimetypes
from urllib.parse import quote as urlquote
import aiohttp
from aiohttp import web

# --- Template Engine V1 (additive): renderer/ + services/ ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in os.sys.path:
    os.sys.path.insert(0, BASE_DIR)
from renderer import renderer as engine  # noqa: E402  (dispatch render_template/load_template)
from renderer.validator import validate_template, validate_photo_value  # noqa: E402
from services import wedding_service, order_service, generation_service, template_service  # noqa: E402

DB_NAME = "undangan.db"
UPLOAD_DIR = "./static_uploads"
HOMEPAGE_FILE = "homepage.html"
PROOF_DIR = os.path.join(BASE_DIR, "payment_proofs")  # privat: TIDAK dipublik via static route
GENERATED_ROOT = os.path.join(BASE_DIR, "generated", "weddings")
os.makedirs(PROOF_DIR, exist_ok=True)
os.makedirs(GENERATED_ROOT, exist_ok=True)

# ============================================================
# S0 SECURITY: konfigurasi via environment variable
#   ADMIN_USERNAME     (default: admin)
#   ADMIN_PASSWORD     (WAJIB diisi di produksi; jika kosong ->
#                       dibuat password acak & dicetak ke log saat startup)
#   SESSION_SECRET     (random secret utk cookie signing; jika kosong ->
#                       digenerate acak tiap start = session hilang saat restart)
#   ALLOW_INSECURE_COOKIE (=1 hanya utk dev lokal tanpa HTTPS;
#                       default 0 => cookie Secure => wajib HTTPS di Railway)
# ============================================================
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").encode() or secrets.token_bytes(32)
ALLOW_INSECURE_COOKIE = os.environ.get("ALLOW_INSECURE_COOKIE", "0") == "1"

# Domain dasar untuk subdomain undangan client: https://[slug].{INVITE_DOMAIN}
INVITE_DOMAIN = os.environ.get("INVITE_DOMAIN", "invite.sukamoto.web.id")

PBKDF2_ITERATIONS = 200_000
SESSION_MAX_AGE = 8 * 3600          # 8 jam
LOGIN_WINDOW_SECONDS = 900          # 15 menit
LOGIN_MAX_ATTEMPTS = 5              # maks 5 percobaan / IP / window
CSRF_MAX_AGE = 3600                 # token CSRF berlaku 1 jam

# Whitelist upload media (perluasan dari perilaku lama, tetap aman)
ALLOWED_UPLOAD_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf"}
ALLOWED_UPLOAD_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

def _escape(value):
    """Escape nilai dinamis sebelum masuk HTML."""
    return html_lib.escape("" if value is None else str(value), quote=True)

def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()

def verify_password(password, stored):
    try:
        salt_b64, hash_b64 = stored.split("$", 1)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(actual, expected)

def make_session_token(admin_id, username):
    payload = "%d|%s|%d" % (admin_id, username, int(time.time()) + SESSION_MAX_AGE)
    sig = hmac.new(SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + sig

def read_session_token(token):
    """Validasi signature + expiry. Return dict admin atau None."""
    try:
        payload, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, hmac.new(SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()):
            return None
        aid_s, uname, exp_s = payload.split("|")
        if int(exp_s) < int(time.time()):
            return None
        return {"id": int(aid_s), "username": uname}
    except Exception:
        return None

def make_csrf_token(admin_id):
    ts = int(time.time())
    msg = "%d|%d" % (admin_id, ts)
    sig = hmac.new(SESSION_SECRET, ("csrf:" + msg).encode(), hashlib.sha256).hexdigest()
    return msg + "." + sig

def check_csrf_token(admin_id, token):
    try:
        msg, sig = token.rsplit(".", 1)
        aid_s, ts_s = msg.split("|")
        if int(ts_s) + CSRF_MAX_AGE < int(time.time()):
            return False
        if int(aid_s) != int(admin_id):
            return False
        return hmac.compare_digest(sig, hmac.new(SESSION_SECRET, ("csrf:" + msg).encode(), hashlib.sha256).hexdigest())
    except Exception:
        return False

def get_admin(request):
    return request.get("_current_admin")

def csrf_field(request):
    adm = get_admin(request)
    token = make_csrf_token(adm["id"]) if adm else ""
    return '<input type="hidden" name="csrf_token" value="%s">' % _escape(token)

@web.middleware
async def security_middleware(request, handler):
    # 1) Proteksi SEMUA route /admin* (GET maupun POST) — tidak bisa lagi
    #    dilewati dengan mengakses URL/POST langsung seperti sebelumnya.
    if request.path.startswith("/admin"):
        token = request.cookies.get("admin_session")
        admin = read_session_token(token) if token else None
        if admin is None:
            if request.path == "/admin/login":
                pass  # ditangani handler login di bawah
            else:
                raise web.HTTPFound("/admin/login")
        request["_current_admin"] = admin
        # 2) Semua mutation POST admin wajib membawa CSRF token yang valid.
        if request.method == "POST" and request.path != "/admin/login":
            content_type = request.headers.get("Content-Type", "")
            if content_type.startswith("multipart/form-data"):
                # Upload file: jangan konsumsi body di sini (nanti dibaca
                # handler via request.multipart()); CSRF divalidasi handler.
                return await handler(request)
            form = await request.post()
            if not check_csrf_token(admin["id"], form.get("csrf_token", "")):
                raise web.HTTPForbidden(text="CSRF token tidak valid. Silakan login ulang.")
    return await handler(request)

# ---- rate limiter login sederhana in-memory (per IP) ----
_login_attempts = {}

def login_rate_limited(ip):
    now = time.time()
    entries = [t for t in _login_attempts.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
    _login_attempts[ip] = entries
    return len(entries) >= LOGIN_MAX_ATTEMPTS

def record_login_attempt(ip):
    _login_attempts.setdefault(ip, []).append(time.time())

def clear_login_attempts(ip):
    _login_attempts.pop(ip, None)

if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

# Buat default homepage.html jika belum ada
if not os.path.exists(HOMEPAGE_FILE):
    default_home = """
        <div class="bio-card">
            <h4>Visual Storyteller | Portrait & Editorial</h4>
            <p>Menyediakan jasa pembuatan undangan digital interaktif dan profesional untuk momen spesial Anda.</p>
            <div class="quote">"Capturing your precious moments with cinematic digital storytelling."</div>
        </div>
    """
    with open(HOMEPAGE_FILE, "w", encoding="utf-8") as f:
        f.write(default_home)

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            subtitle TEXT NOT NULL,
            image_url TEXT NOT NULL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            package_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price TEXT NOT NULL,
            discount TEXT,
            duration TEXT,
            image_url TEXT NOT NULL,
            html_code TEXT NOT NULL,
            is_top10 INTEGER DEFAULT 0,
            FOREIGN KEY (package_id) REFERENCES packages (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS media_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # S0 SECURITY: akun admin sungguhan (menggantikan PIN client-side "110202")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'superadmin',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
    ''')
    
    # ---- Order lifecycle (flow [6] Kirim Pesanan s/d [13] EXPIRED) ----
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,            -- kode pembayaran unik, cth: SKT-A7K2MQ
            slug TEXT UNIQUE NOT NULL,            -- nama pasangan -> https://[slug].invite.sukamoto.web.id
            template_id INTEGER,
            package_id INTEGER,
            couple_name TEXT NOT NULL,            -- "Rian & Siska"
            groom_name TEXT DEFAULT '',
            bride_name TEXT DEFAULT '',
            groom_insta TEXT DEFAULT '',
            bride_insta TEXT DEFAULT '',
            event_date TEXT DEFAULT '',           -- ISO yyyy-mm-dd ( utk penghitung masa aktif )
            event_time TEXT DEFAULT '',
            akkad_date TEXT DEFAULT '',
            reception_date TEXT DEFAULT '',
            venue_name TEXT DEFAULT '',
            venue_address TEXT DEFAULT '',
            maps_url TEXT DEFAULT '',
            photo_url TEXT DEFAULT '',            -- foto cover hasil upload client
            whatsapp TEXT DEFAULT '',             -- WA pemesan / RSVP
            message TEXT DEFAULT '',              -- pesan pembuka undangan
            status TEXT NOT NULL DEFAULT 'pending_payment',
            payment_method TEXT DEFAULT '',
            amount TEXT DEFAULT '',               -- nominal yang harus dibayar
            proof_filename TEXT DEFAULT '',       -- bukti transfer (upload [8])
            reject_reason TEXT DEFAULT '',
            expires_at TEXT DEFAULT '',           -- tanggal EXPIRED ([12]->[13])
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # log riwayat status per order (timeline admin & audit)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    default_settings = [
        ("payment_account", "BCA 1234567890 a.n SUKA MOTO"),
        ("payment_qris", "QRIS via DANA/OVO 085156918852 a.n SUKA MOTO"),
        ("admin_whatsapp", "6285156918852"),
    ]
    cursor.executemany('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', default_settings)

    cursor.execute("PRAGMA table_info(templates)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'html_code' not in columns:
        cursor.execute("ALTER TABLE templates ADD COLUMN html_code TEXT DEFAULT ''")
    if 'discount' not in columns:
        cursor.execute("ALTER TABLE templates ADD COLUMN discount TEXT DEFAULT ''")
    if 'duration' not in columns:
        cursor.execute("ALTER TABLE templates ADD COLUMN duration TEXT DEFAULT ''")
    if 'is_top10' not in columns:
        cursor.execute("ALTER TABLE templates ADD COLUMN is_top10 INTEGER DEFAULT 0")
    # ---- Template Engine V1: migrasi ADDITIVE (jangan hapus kolom lama) ----
    # render_mode: 'legacy' (template lama) | 'placeholder' (template baru dgn {{key}})
    # template_path boleh NULL selama html_code masih source of truth.
    if 'render_mode' not in columns:
        cursor.execute("ALTER TABLE templates ADD COLUMN render_mode TEXT NOT NULL DEFAULT 'legacy'")
    if 'template_key' not in columns:
        cursor.execute("ALTER TABLE templates ADD COLUMN template_key TEXT")
    if 'template_path' not in columns:
        cursor.execute("ALTER TABLE templates ADD COLUMN template_path TEXT")
    if 'status' not in columns:
        cursor.execute("ALTER TABLE templates ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    cursor.close()
    conn.close()
    # weddings + payment_state (tabel baru, additive; idempoten)
    template_service.migrate_additive(DB_NAME)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) FROM packages')
    if cursor.fetchone()[0] == 0:
        initial_pkgs = [
            ('Silver Package', 'Digital Undangan Basic', 'https://images.unsplash.com/photo-1519741497674-611481863552?auto=format&fit=crop&w=600&q=80'),
            ('Gold Package', 'Custom Domain + RSVP', 'https://images.unsplash.com/photo-1511285560929-80b456fea0bc?auto=format&fit=crop&w=600&q=80'),
            ('Platinum VIP', 'All-in-One Exclusive', 'https://images.unsplash.com/photo-1465495976277-4387d4b0b4c6?auto=format&fit=crop&w=600&q=80')
        ]
        cursor.executemany('INSERT INTO packages (name, subtitle, image_url) VALUES (?, ?, ?)', initial_pkgs)
        conn.commit()

    cursor.execute('SELECT COUNT(*) FROM templates')
    if cursor.fetchone()[0] == 0:
        sample_html = """<!DOCTYPE html><html lang="id"><head><meta charset="UTF-8"><title>Undangan Pernikahan</title><style>body{background:#fdfbf7;font-family:serif;text-align:center;padding:40px;color:#333;}h1{color:#b8860b;font-size:36px;margin-bottom:5px;}.editable{border:1px dashed #d4af37;padding:5px;display:inline-block;margin:5px;background:#fff;min-width:150px;}</style></head><body><h1>The Wedding of</h1><h2 contenteditable="true" class="editable">Rian & Siska</h2><p>Hari/Tanggal: <span contenteditable="true" class="editable">12 Desember 2026</span></p><p>Bertempat di: <span contenteditable="true" class="editable">Gedung Kencana Cirebon</span></p><hr style="width:50%;margin:20px auto;"><p>Merupakan suatu kehormatan dan kebahagiaan bagi kami apabila Bapak/Ibu berkenan hadir.</p></body></html>"""
        initial_tmpls = [
            (1, 'Classic Floral', 'Rp 150.000', 'Diskon 10%', 'Aktif 6 Bulan', 'https://images.unsplash.com/photo-1520854221256-17451cc331bf?auto=format&fit=crop&w=400&q=80', sample_html, 1),
            (1, 'Minimalist Monokrom', 'Rp 175.000', '', 'Aktif 6 Bulan', 'https://images.unsplash.com/photo-1519225421980-715cb0215aed?auto=format&fit=crop&w=400&q=80', sample_html, 1),
            (2, 'Rustic Wood', 'Rp 300.000', 'Promo Launching', 'Aktif 1 Tahun', 'https://images.unsplash.com/photo-1511795409834-ef04bbd61622?auto=format&fit=crop&w=400&q=80', sample_html, 1),
            (2, 'Modern Gold Luxury', 'Rp 350.000', 'Best Seller', 'Aktif Selamanya', 'https://images.unsplash.com/photo-1532712938310-34cb3982ef74?auto=format&fit=crop&w=400&q=80', sample_html, 1),
        ]
        cursor.executemany('INSERT INTO templates (package_id, name, price, discount, duration, image_url, html_code, is_top10) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', initial_tmpls)
        conn.commit()

    # S0 SECURITY: pastikan ada minimal 1 akun admin.
    # Password diambil dari env ADMIN_PASSWORD; jika kosong -> digenerate acak
    # dan dicetak sekali ke log startup (tidak pernah disimpan plaintext).
    global _BOOT_ADMIN_HINT
    cursor.execute("SELECT COUNT(*) FROM admin_users")
    if cursor.fetchone()[0] == 0:
        pwd = ADMIN_PASSWORD or secrets.token_urlsafe(9)
        cursor.execute('INSERT INTO admin_users (username, password_hash, role) VALUES (?, ?, ?)',
                       (ADMIN_USERNAME, hash_password(pwd), 'superadmin'))
        conn.commit()
        if not ADMIN_PASSWORD:
            _BOOT_ADMIN_HINT = ("ADMIN login dibuat otomatis -> username: %s | password sementara: %s"
                                % (ADMIN_USERNAME, pwd))
    else:
        if ADMIN_PASSWORD:
            cursor.execute('UPDATE admin_users SET password_hash = ? WHERE username = ?',
                           (hash_password(ADMIN_PASSWORD), ADMIN_USERNAME))
            conn.commit()
        cursor.execute("SELECT username FROM admin_users LIMIT 1")
        row = cursor.fetchone()
        _BOOT_ADMIN_HINT = None if row else "Tidak ada akun admin di database!"
    conn.close()

_BOOT_ADMIN_HINT = None
init_db()

if _BOOT_ADMIN_HINT:
    print("=" * 60)
    print("[S0][SECURITY]", _BOOT_ADMIN_HINT)
    print("[S0][SECURITY] Segera ganti via env ADMIN_PASSWORD atau fitur ganti password.")
    print("=" * 60)

# ============================================================
# ORDER LIFECYCLE HELPERS
#   pending_payment -> awaiting_verification -> verified
#       -> processing -> active -> expired
#   awaiting_verification --(reject)--> rejected_payment -> awaiting_verification
# ============================================================
import datetime as _dt

ORDER_STATUS_LABEL = {
    "pending_payment":      "[7] Menunggu Pembayaran",
    "awaiting_verification":"[9] Menunggu Verifikasi Pembayaran",
    "rejected_payment":     "[9] Ditolak - Perbaiki Pembayaran",
    "verified":             "[10] Pembayaran Diverifikasi",
    "processing":           "[10] Undangan Diproses",
    "active":               "[11] Undangan Aktif",
    "expired":              "[13] EXPIRED",
}

def get_setting(cursor, key, default=""):
    cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
    row = cursor.fetchone()
    return row[0] if row else default

def make_order_code():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "SKT-" + "".join(secrets.choice(alphabet) for _ in range(6))

def slugify(value):
    """'Rian & Siska' -> 'rian-siska' (subdomain https://[slug].invite.sukamoto.web.id)."""
    s = (value or "").lower()
    s = s.replace("&", " dan ")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40]

def unique_slug(cursor, base):
    if not base:
        base = "undangan"
    slug, n = base, 1
    while cursor.execute('SELECT 1 FROM orders WHERE slug = ?', (slug,)).fetchone():
        n += 1
        slug = "%s-%d" % (base, n)
    return slug

def add_order_event(cursor, order_id, status, note=""):
    cursor.execute('INSERT INTO order_events (order_id, status, note) VALUES (?, ?, ?)',
                   (order_id, status, note))

def set_order_status(cursor, order_id, status, note=""):
    cursor.execute("UPDATE orders SET status = ?, reject_reason = '', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                   (status, order_id))
    add_order_event(cursor, order_id, status, note)

def parse_amount_rupiah(text):
    """'Rp 250.000' -> 250000 ; gagal -> 0."""
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else 0

def duration_days_from_text(duration, price_text=""):
    """Ambil angka hari dari keterangan durasi paket/template."""
    d = (duration or "").lower()
    m = re.search(r"(\d+)\s*(hari|bulan|minggu|tahun)", d)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return n * {"hari": 1, "minggu": 7, "bulan": 30, "tahun": 365}[unit]
    if "selamanya" in d or "seumur hidup" in d:
        return 36500  # ~100 tahun
    p = (price_text or "").lower()
    if "platinum" in p or "vip" in p:
        return 180
    if "gold" in p:
        return 90
    if "silver" in p:
        return 30
    return 30

def compute_expires_at(order_row):
    """Masa aktif mulai dari tanggal acara; fallback: sejak diaktifkan."""
    _, tmpl_dur, pkg_dur, event_date, amount = order_row
    days = duration_days_from_text(tmpl_dur)
    days2 = duration_days_from_text(pkg_dur)
    days = min(days, days2) if (tmpl_dur and pkg_dur) else max(days, days2)
    start = None
    try:
        start = _dt.date.fromisoformat(event_date)
    except Exception:
        start = None
    if start is None:
        start = _dt.date.today()
    return (start + _dt.timedelta(days=days)).isoformat()

def refresh_expired_orders():
    """[12] Masa Aktif Berjalan -> [13] EXPIRED otomatis saat melewati expires_at."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id FROM orders WHERE status = 'active' AND expires_at != '' AND expires_at < ?",
                (_dt.date.today().isoformat(),))
    ids = [r[0] for r in cur.fetchall()]
    for oid in ids:
        cur.execute("UPDATE orders SET status = 'expired', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (oid,))
        add_order_event(cur, oid, "expired", "Masa aktif habis (otomatis)")
    conn.commit()
    conn.close()
    return ids

def invite_url_for(slug):
    return "https://%s.%s" % (slug, INVITE_DOMAIN)

def wa_admin_link(order_row, approve=True):
    """Link chat WA admin berisi laporan pesanan siap kirim (sisi admin)."""
    (oid, code, slug, cname, wa, amount, status) = order_row[:7]
    url = invite_url_for(slug)
    if approve:
        msg = ("Halo %s, pembayaran undangan dengan kode %s sudah KAMI VERIFIKASI.\n"
               "Undangan Anda kini AKTIF di: %s\n"
               "Silakan bagikan ke para tamu. Terima kasih telah menggunakan SUKA MOTO!" % (cname, code, url))
    else:
        msg = ("Halo %s, mohon maaf bukti pembayaran untuk kode %s belum dapat kami verifikasi.\n"
               "Silakan periksa kembali dan upload ulang bukti transfernya melalui halaman status pesanan Anda.\n"
               "Terima kasih. - SUKA MOTO") % (cname, code)
    admin_wa = ""
    conn = sqlite3.connect(DB_NAME)
    admin_wa = get_setting(conn.cursor(), "admin_whatsapp", "6285156918852")
    conn.close()
    return "https://wa.me/%s?text=%s" % (re.sub(r"\D", "", admin_wa), urlquote(msg))

MONTHS_ID = ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
             "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
DAYS_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

def format_date_id(iso):
    try:
        d = _dt.date.fromisoformat(iso)
        return "%s, %d %s %d" % (DAYS_ID[d.weekday()], d.day, MONTHS_ID[d.month - 1], d.year)
    except Exception:
        return iso or "-"

def render_invitation_html(html_code, o):
    """Injeksi data undangan ke dalam html_code template.

    LEGACY (flow lama /preview & /u/{slug}): mendukung 2 gaya:
      1) token {{nama_field}} di dalam HTML template
      2) atribut data-field="nama_field" pada elemen (untuk template lama contenteditable)

    ENGINE V1: bila ada pasangan wedding <-> order <-> template dengan
    render_mode='placeholder', gunakan renderer kanonik (engine.render_template)
    agar PREVIEW client == GENERATION production (one renderer).
    """
    # --- jalur Template Engine V1 (additive; fallback ke perilaku lama) ---
    try:
        _tid = o.get("template_id")
        if _tid is not None and o.get("_use_engine"):
            _conn = sqlite3.connect(DB_NAME)
            try:
                _tmpl = engine.load_template(_conn, _tid)
                if _tmpl and (_tmpl.get("render_mode") or "legacy") == "placeholder":
                    _w = wedding_service.get_wedding_by_order(_conn, o["id"]) if o.get("id") else None
                    if _w is None:
                        _w = {
                            "groom_name": o.get("groom_name", ""), "bride_name": o.get("bride_name", ""),
                            "event_date": o.get("event_date", ""), "event_time": o.get("event_time", ""),
                            "venue": o.get("venue_name", ""), "address": o.get("venue_address", ""),
                            "couple_photo": o.get("photo_url", ""),
                        }
                    return engine.render_template(_tmpl, wedding_service.to_renderer_data(_w))
            finally:
                _conn.close()
    except engine.RenderError:
        raise
    except Exception as _e:  # jangan jatuhkan flow lama karena error engine
        print("[renderer-v1] fallback legacy render:", _e)

    tokens = {
        "couple_name": o["couple_name"],
        "groom_name": o["groom_name"],
        "bride_name": o["bride_name"],
        "groom_insta": "@" + o["groom_insta"].lstrip("@") if o["groom_insta"] else "",
        "bride_insta": "@" + o["bride_insta"].lstrip("@") if o["bride_insta"] else "",
        "event_date": format_date_id(o["event_date"]),
        "event_time": o["event_time"],
        "akkad_date": format_date_id(o["akkad_date"] or o["event_date"]),
        "reception_date": format_date_id(o["reception_date"] or o["event_date"]),
        "venue_name": o["venue_name"],
        "venue_address": o["venue_address"],
        "maps_url": o["maps_url"] or "#",
        "photo_url": o["photo_url"],
        "whatsapp": o["whatsapp"],
        "message": o["message"],
        "code": o["code"],
        "link": invite_url_for(o["slug"]),
    }
    out = html_code or ""
    for k, v in tokens.items():
        out = out.replace("{{%s}}" % k, _escape(v))

    def fill_data_fields(fragment):
        def repl(m):
            tag, field = m.group(1), m.group(2)
            val = tokens.get(field, "")
            low = tag.lower()
            if 'src="' in low or "src='" in low:
                tag = re.sub(r"(src=)(\"[^\"]*\"|'[^']*')", lambda _: 'src="%s"' % _escape(val), tag, count=1)
            elif 'href="' in low or "href='" in low:
                tag = re.sub(r"(href=)(\"[^\"]*\"|'[^']*')", lambda _: 'href="%s"' % _escape(val), tag, count=1)
            else:
                tag = re.sub(r">\s*$", ">", tag)
                tag = tag + _escape(val) + "</%s>" % re.match(r"<\s*([a-zA-Z0-9]+)", low).group(1)
            return tag
        return re.sub(r"(<[a-zA-Z0-9]+[^>]*?\bdata-field=[\"']([a-z_]+)[\"'][^>]*?>)", repl, fragment)

    out = fill_data_fields(out)
    # bersihkan token yang tidak terisi agar client tidak melihat "{{...}}"
    out = re.sub(r"\{\{[a-z_]+\}\}", "", out)
    return out

# ---- UI bantu untuk alur client (stepper + kartu status pesanan) ----
WIZARD_STEPS = ["Landing", "Paket", "Template", "Data", "Preview", "Kirim", "Bayar", "Bukti", "Verifikasi", "Diproses", "Aktif"]

def stepbar_html(active_no):
    items = ""
    for i, label in enumerate(WIZARD_STEPS, start=1):
        state = "done" if i < active_no else ("now" if i == active_no else "todo")
        color = "#34d399" if state == "done" else ("#fbbf24" if state == "now" else "#3f3f46")
        bg = "#18181b"
        items += ('<div style="flex:1; text-align:center;">'
                  '<div style="width:20px;height:20px;line-height:20px;margin:0 auto;border-radius:50%%;'
                  'background:%s;color:#000;font-size:10px;font-weight:800;">%s</div>'
                  '<div style="font-size:8px;color:%s;margin-top:3px;white-space:nowrap;">%s</div></div>') % (
                  color, ("%d" % i) if state != "done" else "&#10003;", color, label)
    return ('<div style="display:flex;gap:2px;background:%s;border:1px solid #27272a;border-radius:10px;'
            'padding:10px 6px;margin-bottom:18px;overflow-x:auto;">%s</div>') % (bg, items)

ORDER_CSS = """
    .order-card { background: #18181b; border: 1px solid #27272a; border-radius: 14px; padding: 18px; margin-bottom: 15px; }
    .order-card h3 { font-size: 14px; color: #fff; margin-bottom: 10px; }
    .o-row { display: flex; justify-content: space-between; font-size: 12px; color: #a1a1aa; margin-bottom: 8px; gap: 10px; }
    .o-row strong { color: #fff; text-align: right; word-break: break-all; }
    .status-pill { display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 10px; font-weight: 800; }
    .st-pending_payment { background:#78350f; color:#fcd34d; }
    .st-awaiting_verification { background:#1e3a8a; color:#93c5fd; }
    .st-rejected_payment { background:#7f1d1d; color:#fecaca; }
    .st-verified { background:#14532d; color:#86efac; }
    .st-processing { background:#3b0764; color:#d8b4fe; }
    .st-active { background:#064e3b; color:#34d399; }
    .st-expired { background:#27272a; color:#a1a1aa; }
    .big-code { font-size: 20px; letter-spacing: 2px; color: #fbbf24; font-weight: 800; text-align: center; background: #121215; border: 1px dashed #fbbf24; padding: 10px; border-radius: 8px; margin: 10px 0; }
    .btn-primary { display:block; width:100%; background:#fbbf24; color:#000; font-weight:800; padding:12px; border:none; border-radius:8px; font-size:13px; cursor:pointer; text-align:center; text-decoration:none; margin-top:10px; }
    .btn-secondary { display:block; width:100%; background:#27272a; color:#fff; font-weight:700; padding:11px; border:1px solid #3f3f46; border-radius:8px; font-size:12px; cursor:pointer; text-align:center; text-decoration:none; margin-top:8px; }
    .btn-wa { display:block; width:100%; background:#22c55e; color:#fff; font-weight:800; padding:12px; border:none; border-radius:8px; font-size:13px; text-align:center; text-decoration:none; margin-top:10px; }
    input, select, textarea { padding: 10px; margin: 5px 0; border-radius: 8px; border: 1px solid #3f3f46; background: #121215; color: #fff; width: 100%; font-size: 12px; }
    label.fld { font-size: 11px; color: #a1a1aa; display: block; margin-top: 8px; }
"""

def order_status_card(o, show_actions=True):
    """o = dict row orders. Render kartu status sesuai langkah [7]-[13]."""
    status = o["status"]
    pill = '<span class="status-pill st-%s">%s</span>' % (status, _escape(ORDER_STATUS_LABEL.get(status, status)))
    url = invite_url_for(o["slug"])
    html = '<div class="order-card"><h3>Pesanan %s &nbsp; %s</h3>' % (_escape(o["code"]), pill)
    html += '<div class="o-row"><span>Nama Pasangan</span><strong>%s</strong></div>' % _escape(o["couple_name"])
    html += '<div class="o-row"><span>Nomor WhatsApp</span><strong>%s</strong></strong></div>' % _escape(o["whatsapp"])
    html += '<div class="o-row"><span>Total Pembayaran</span><strong style="color:#34d399;">%s</strong></div>' % _escape(o["amount"])
    if status == "active":
        remaining = "-"
        try:
            d = (_dt.date.fromisoformat(o["expires_at"]) - _dt.date.today()).days
            remaining = "%d hari lagi" % max(d, 0)
        except Exception:
            pass
        html += ('<div class="o-row"><span>Link Undangan</span><strong><a href="%s" target="_blank" '
                 'style="color:#34d399;">%s</a></strong></div>' % (_escape(url), _escape(url)))
        html += '<div class="o-row"><span>Masa Aktif</span><strong>s/d %s (%s)</strong></div>' % (
            _escape(format_date_id(o["expires_at"])), _escape(remaining))
        html += ('<div style="font-size:10px;color:#71717a;margin-top:6px;">[12] Masa aktif berjalan otomatis. '
                 'Setelah melewati tanggal di atas undangan menjadi EXPIRED.</div>')
    elif status == "expired":
        html += '<div class="o-row"><span>Link Undangan</span><strong style="color:#a1a1aa;">%s (nonaktif)</strong></div>' % _escape(url)
        html += '<div style="font-size:11px;color:#ef4444;font-weight:bold;margin-top:6px;">[13] MASA AKTIF UNDANGAN SUDAH HABIS (EXPIRED).</div>'
        html += '<div style="font-size:11px;color:#a1a1aa;">Hubungi admin via WhatsApp untuk perpanjangan paket.</div>'
    elif status == "pending_payment":
        html += '<div class="big-code">%s</div>' % _escape(o["code"])
        html += ('<div style="font-size:11px;color:#a1a1aa;">Transfer sesuai nominal lalu tulis <b>kode pembayaran unik</b> '
                 'di atas pada berita transfer, kemudian upload bukti pembayaran.</div>')
    elif status == "awaiting_verification":
        html += '<div class="o-row"><span>Bukti Pembayaran</span><strong>Terupload, menunggu verifikasi admin</strong></div>'
        html += '<div style="font-size:11px;color:#93c5fd;margin-top:6px;"><i class="fa-regular fa-hourglass-half"></i> '
        'Admin akan memeriksa transfer Anda. Status halaman ini akan berubah setelah diverifikasi.</div>'
    elif status == "rejected_payment":
        html += '<div style="background:#7f1d1d;color:#fecaca;font-size:11px;padding:10px;border-radius:8px;margin-top:8px;">'
        '&#10060; Bukti pembayaran ditolak: %s<br>Silakan perbaiki dan upload ulang bukti transfer di bawah.</div>' % _escape(o["reject_reason"] or "data transfer tidak cocok")
    elif status in ("verified", "processing"):
        html += '<div class="o-row"><span>Tahap</span><strong>%s</strong></div>' % _escape(ORDER_STATUS_LABEL.get(status, status))
        html += '<div style="font-size:11px;color:#d8b4fe;margin-top:6px;">Undangan Anda sedang kami proses. '
        'Link final akan dikirim ke WhatsApp <b>%s</b>.</div>' % _escape(o["whatsapp"])
    if o.get("proof_filename"):
        purl = "/static_uploads/" + urlquote(os.path.basename(o["proof_filename"]))
        html += '<div style="margin-top:10px;font-size:10px;color:#71717a;">Bukti bayar terakhir: <a href="%s" target="_blank" style="color:#fbbf24;">lihat gambar</a></div>' % _escape(purl)
    html += '</div>'
    return html


BASE_HEAD = """
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SUKA MOTO | Visual Storyteller</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Alex+Brush&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', sans-serif; }
        body { background: #09090b; color: #f4f4f5; display: flex; justify-content: center; }
        .container { width: 100%; max-width: 480px; min-height: 100vh; background: #0c0c0e; padding: 20px; border-left: 1px solid #27272a; border-right: 1px solid #27272a; position: relative; display: flex; flex-direction: column; justify-content: space-between; }
        .content-wrap { flex: 1; }
        .navbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px; padding-bottom: 12px; border-bottom: 1px solid #27272a; }
        .brand-group { display: flex; flex-direction: column; text-decoration: none; }
        .brand-main { font-size: 15px; font-weight: 800; color: #fff; letter-spacing: 1px; }
        .brand-sub { font-family: 'Alex Brush', cursive; font-size: 22px; color: #fbbf24; margin-top: -8px; line-height: 1; }
        .nav-actions { display: flex; align-items: center; gap: 8px; }
        .hamburger-btn { background: none; border: none; color: #a1a1aa; font-size: 16px; cursor: pointer; padding: 6px; border-radius: 6px; transition: 0.2s; }
        .hamburger-btn:hover { color: #fbbf24; background: #18181b; }
        .menu-dropdown { display: none; position: absolute; top: 55px; right: 20px; background: #18181b; border: 1px solid #27272a; border-radius: 8px; width: 180px; z-index: 100; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.5); }
        .menu-dropdown a { display: flex; align-items: center; gap: 8px; padding: 10px 12px; color: #f4f4f5; text-decoration: none; font-size: 11px; border-bottom: 1px solid #27272a; }
        .menu-dropdown a:last-child { border-bottom: none; }
        .menu-dropdown a:hover { background: #27272a; color: #fbbf24; border-radius: 8px; }
        .section-title { font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: #fbbf24; margin-bottom: 15px; font-weight: bold; }
        .footer { margin-top: 40px; border-top: 1px solid #27272a; padding: 25px 0 15px 0; text-align: center; }
        .footer-brand { font-size: 13px; font-weight: bold; color: #fff; margin-bottom: 6px; }
        .admin-footer-btn { background: none; border: none; color: #71717a; cursor: pointer; font-size: 12px; padding: 2px 6px; border-radius: 4px; transition: 0.2s; margin-left: 6px; }
        .admin-footer-btn:hover { color: #fbbf24; background: #18181b; }
        .footer-tagline { font-size: 11px; color: #a1a1aa; margin-bottom: 15px; }
        .social-icons { display: flex; justify-content: center; gap: 15px; margin-bottom: 15px; }
        .social-icons a { background: #18181b; border: 1px solid #27272a; width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fbbf24; font-size: 13px; text-decoration: none; transition: background 0.2s; }
        .social-icons a:hover { background: #fbbf24; color: #000; }
        .footer-web { font-size: 11px; color: #34d399; text-decoration: none; font-weight: 600; display: inline-block; margin-bottom: 15px; }
        .copyright { font-size: 10px; color: #71717a; border-top: 1px solid #18181b; padding-top: 12px; display: flex; align-items: center; justify-content: center; gap: 4px; }
    </style>
"""

FOOTER_HTML = """
    <div class="footer">
        <div class="footer-brand">
            SUKA MOTO
        </div>
        <div class="footer-tagline">Visual storyteller | Portrait & editorial</div>
        <div class="social-icons">
            <a href="https://instagram.com/sukaamotoo" target="_blank"><i class="fa-brands fa-instagram"></i></a>
            <a href="https://facebook.com" target="_blank"><i class="fa-brands fa-facebook-f"></i></a>
            <a href="https://wa.me/6285156918852" target="_blank"><i class="fa-brands fa-whatsapp"></i></a>
        </div>
        <a href="https://www.sukamoto.web.id" target="_blank" class="footer-web"><i class="fa-solid fa-globe"></i> www.sukamoto.web.id</a>
        <div class="copyright">
            &copy; 2026 SUKA MOTO. All Rights Reserved.
            <button class="admin-footer-btn" onclick="accessAdmin()" title="Admin Control"><i class="fa-solid fa-shield-halved"></i></button>
        </div>
    </div>
"""

ADMIN_SECURITY_SCRIPT = """
    <script>
        // S0 SECURITY: PIN client-side "110202" telah DIHAPUS dari kode.
        // Admin kini dilindungi login server-side di /admin/login
        // (session cookie HttpOnly + CSRF + rate-limit). Tombol footer
        // cukup mengarahkan ke halaman login; autentikasi terjadi di server.
        function accessAdmin() {
            window.location.href = "/admin/login";
        }
        function toggleMenu() {
            var menu = document.getElementById("menuDropdown");
            menu.style.display = menu.style.display === "block" ? "none" : "block";
        }
        window.onclick = function(event) {
            if (!event.target.matches('.hamburger-btn') && !event.target.closest('.hamburger-btn')) {
                var menu = document.getElementById("menuDropdown");
                if (menu) menu.style.display = "none";
            }
        }
    </script>
"""

# ============================================================
# ALUR CLIENT: [1] Landing -> [2] Paket -> [3] Template -> [4] Data
#   -> [5] Preview -> [6] Kirim Pesanan -> [7] Pembayaran -> [8] Bukti
#   -> [9] Verifikasi -> [10] Diproses -> [11] Aktif -> [12] Masa Aktif -> [13] EXPIRED
# ============================================================
def client_page(title, step_no, body_html, extra_head=""):
    return """<!DOCTYPE html>
<html lang="id">
<head>
%s
<title>%s | SUKA MOTO Invitation</title>
<style>%s</style>
%s
</head>
<body>
<div class="container">
  <div class="content-wrap">
    <div class="navbar">
      <a href="/" class="brand-group"><span class="brand-main">SUKA MOTO</span><span class="brand-sub">Invitation</span></a>
      <div class="nav-actions"><a href="/my-orders" style="font-size:10px;color:#fbbf24;text-decoration:none;font-weight:bold;">Cek Pesanan</a></div>
    </div>
    %s
    %s
  </div>
  %s
</div>
</body>
</html>""" % (BASE_HEAD, _escape(title), ORDER_CSS, extra_head,
             stepbar_html(step_no) if step_no else "", body_html, FOOTER_HTML)

def fetch_order(conn, code=None, oid=None):
    cur = conn.cursor()
    if code:
        cur.execute("SELECT * FROM orders WHERE code = ?", (code.upper(),))
    else:
        cur.execute("SELECT * FROM orders WHERE id = ?", (oid,))
    row = cur.fetchone()
    if not row:
        return None
    return dict(zip([c[0] for c in cur.description], row))

async def handle_start(request):
    """[2] Pilih Paket."""
    conn = sqlite3.connect(DB_NAME)
    pkgs = conn.execute('SELECT id, name, subtitle, image_url FROM packages ORDER BY id').fetchall()
    conn.close()
    pkg_meta = {1: ("Silver", "Aktif 30 hari"), 2: ("Gold", "Aktif 90 hari"), 3: ("Platinum VIP", "Aktif 6 bulan")}
    cards = ""
    for pid, name, sub, img in pkgs:
        tag, dur = pkg_meta.get(pid, (name, "Paket pilihan"))
        cards += """
        <a href="/templates?package_id=%d&from=start" style="background:#18181b;border:1px solid #27272a;border-radius:14px;overflow:hidden;text-decoration:none;display:block;margin-bottom:12px;">
          <img src="%s" alt="" style="width:100%%;height:110px;object-fit:cover;display:block;">
          <div style="padding:12px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <h3 style="font-size:13px;color:#fff;font-weight:800;">%s</h3>
              <span style="font-size:9px;background:#3f3f46;color:#fbbf24;padding:2px 8px;border-radius:99px;font-weight:bold;">%s</span>
            </div>
            <p style="font-size:11px;color:#a1a1aa;margin:4px 0 8px;">%s</p>
            <div style="font-size:11px;color:#34d399;font-weight:bold;"><i class="fa-regular fa-clock"></i> %s &bull; Lihat Koleksi Template &rarr;</div>
          </div>
        </a>""" % (pid, _escape(img), _escape(name), _escape(tag), _escape(sub), _escape(dur))
    body = """
    <div class="section-title">Langkah 2 &mdash; Pilih Paket</div>
    <div class="order-card" style="border-left:3px solid #fbbf24;">
      <h3>Paket Undangan</h3>
      <p style="font-size:11px;color:#a1a1aa;line-height:1.6;">Setelah memilih paket, Anda akan diarahkan ke halaman
      <b>Kirim Pesanan</b> untuk menyelesaikan pembayaran. Link undangan memakai subdomain
      <span style="color:#34d399;">nama-pasangan.%s</span>.</p>
    </div>
    %s
    <a href="/" class="btn-secondary">&larr; Kembali ke Beranda</a>
    """ % (_escape(INVITE_DOMAIN), cards)
    return web.Response(text=client_page("Pilih Paket", 2, body), content_type="text/html")

async def handle_form(request):
    """[4] Isi Data Undangan."""
    tmpl_id = request.query.get("template_id", "")
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('SELECT t.id, t.name, t.price, t.discount, t.duration, p.id, p.name '
                'FROM templates t JOIN packages p ON t.package_id = p.id WHERE t.id = ?', (tmpl_id,))
    tmpl = cur.fetchone()
    conn.close()
    if not tmpl:
        raise web.HTTPFound("/start")
    tid, tname, tprice, tdisc, tdur, pid, pname = tmpl
    saved = request.query  # nilai kembali jika validasi gagal
    def v(key, default=""):
        return _escape(saved.get(key, default))
    body = """
    <div class="section-title">Langkah 4 &mdash; Isi Data Undangan</div>
    <div class="order-card" style="border-left:3px solid #34d399;">
      <div style="display:flex;justify-content:space-between;font-size:12px;">
        <span style="color:#a1a1aa;">Template Terpilih</span>
        <strong style="color:#fff;">%(tname)s <span style="color:#34d399;">(%(tprice)s)</span></strong>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:12px;margin-top:6px;">
        <span style="color:#a1a1aa;">Paket / Durasi</span><strong style="color:#fff;">%(pname)s &bull; %(tdur)s</strong>
      </div>
    </div>
    <form action="/preview" method="POST" class="order-card">
      <input type="hidden" name="template_id" value="%(tid)s">
      <h3>Mempelai</h3>
      <label class="fld">Nama Pasangan ( utk link: nama-pasangan.%(dom)s ) *</label>
      <input type="text" name="couple_name" required placeholder="Contoh: Rian & Siska" value="%(couple_name)s">
      <label class="fld">Nama Mempelai Pria *</label>
      <input type="text" name="groom_name" required placeholder="Nama lengkap + gelar" value="%(groom_name)s">
      <label class="fld">Nama Mempelai Wanita *</label>
      <input type="text" name="bride_name" required placeholder="Nama lengkap + gelar" value="%(bride_name)s">
      <label class="fld">Instagram Pria</label>
      <input type="text" name="groom_insta" placeholder="@username" value="%(groom_insta)s">
      <label class="fld">Instagram Wanita</label>
      <input type="text" name="bride_insta" placeholder="@username" value="%(bride_insta)s">

      <h3 style="margin-top:16px;">Acara &amp; Lokasi</h3>
      <label class="fld">Tanggal Akad Nikah *</label>
      <input type="date" name="akkad_date" required value="%(akkad_date)s">
      <label class="fld">Tanggal Resepsi *</label>
      <input type="date" name="reception_date" required value="%(reception_date)s">
      <label class="fld">Waktu Acara</label>
      <input type="text" name="event_time" placeholder="Contoh: 10.00 - 14.00 WIB" value="%(event_time)s">
      <label class="fld">Nama Gedung / Tempat *</label>
      <input type="text" name="venue_name" required placeholder="Gedung Kencana" value="%(venue_name)s">
      <label class="fld">Alamat Lengkap *</label>
      <textarea name="venue_address" rows="2" required placeholder="Jl. ... , Kota ...">%(venue_address)s</textarea>
      <label class="fld">Link Lokasi Google Maps</label>
      <input type="url" name="maps_url" placeholder="https://maps.google.com/..." value="%(maps_url)s">

      <h3 style="margin-top:16px;">Foto &amp; WhatsApp</h3>
      <label class="fld">Upload Foto Cover Mempelai (jpg/png/webp, maks 5 MB)</label>
      <input type="file" name="photo" accept=".jpg,.jpeg,.png,.webp,.gif" style="background:#121215;padding:6px;">
      <label class="fld">Nomor WhatsApp Pemesan / RSVP *</label>
      <input type="tel" name="whatsapp" required placeholder="08123456789" value="%(whatsapp)s">
      <label class="fld">Pesan Pembuka Undangan</label>
      <textarea name="message" rows="2" placeholder="Tanpa mengurangi rasa hormat...">%(message)s</textarea>
      <button type="submit" class="btn-primary"><i class="fa-regular fa-eye"></i> Lanjut ke Preview &rarr;</button>
    </form>
    <a href="/template-action?id=%(tid)s" class="btn-secondary">&larr; Ganti Template</a>
    """ % dict(dom=_escape(INVITE_DOMAIN), tid=tid, tname=_escape(tname), tprice=_escape(tprice),
               pname=_escape(pname), tdur=_escape(tdur or "-"),
               **{k: v(k) for k in ["couple_name", "groom_name", "bride_name", "groom_insta", "bride_insta",
                                    "akkad_date", "reception_date", "event_time", "venue_name",
                                    "venue_address", "maps_url", "whatsapp", "message"]})
    return web.Response(text=client_page("Isi Data Undangan", 4, body), content_type="text/html")

async def handle_preview(request):
    """[5] Preview — render template dengan data yang baru diisi (+ upload foto)."""
    data = await request.post()
    tmpl_id = data.get("template_id", "")
    couple = (data.get("couple_name") or "").strip()
    errors = []
    if not couple:
        errors.append("Nama pasangan wajib diisi.")
    if not re.match(r"^[0-9+\-\s]{8,16}$", (data.get("whatsapp") or "").strip()):
        errors.append("Nomor WhatsApp tidak valid (contoh: 08123456789).")
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('SELECT t.id, t.name, t.price, t.duration, t.html_code, p.id, p.name, p.duration '
                'FROM templates t JOIN packages p ON t.package_id = p.id WHERE t.id = ?', (tmpl_id,))
    tmpl = cur.fetchone()
    if errors or not tmpl:
        conn.close()
        qs = urlquote("&".join("%s=%s" % (k, urlquote(str(val))) for k, val in data.items()))
        msg = " | ".join(errors) if errors else "Template tidak ditemukan."
        raise web.HTTPFound("/form?template_id=%s&error=%s&%s" % (urlquote(str(tmpl_id)), urlquote(msg), qs))

    _, tname, tprice, tdur, html_code, pid, pname, _pkgdur = tmpl
    # upload foto cover (validasi sama seperti file manager admin)
    photo_url = ""
    reader = request.content_type.startswith("multipart") and await request.multipart() or None
    if reader:
        field = await reader.next()
        while field is not None:
            if field.name == "photo" and field.filename:
                original = os.path.basename(field.filename)
                ext = os.path.splitext(original)[1].lower()
                declared = (field.headers.get("Content-Type") if field.headers else "").split(";")[0].strip().lower()
                guessed = mimetypes.guess_type(original)[0] or ""
                if ext in ALLOWED_UPLOAD_EXTS and (not declared or declared in ALLOWED_UPLOAD_MIMES) \
                        and (not guessed or guessed in ALLOWED_UPLOAD_MIMES):
                    safe_stem = re.sub(r"[^A-Za-z0-9_-]", "_", os.path.splitext(original)[0])[:40] or "foto"
                    saved_name = "client_%s_%d%s" % (safe_stem, int(time.time() * 1000), ext)
                    fpath = os.path.join(UPLOAD_DIR, saved_name)
                    size, oversize = 0, False
                    with open(fpath, "wb") as f:
                        while True:
                            chunk = await field.read_chunk()
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > MAX_UPLOAD_BYTES:
                                oversize = True
                                break
                            f.write(chunk)
                    if oversize or size == 0:
                        try:
                            os.remove(fpath)
                        except OSError:
                            pass
                    else:
                        photo_url = "/static_uploads/" + urlquote(saved_name)
                break
            field = await reader.next()

    o = {
        "id": None,  # belum ada order; engine V1 pakai fallback mapping di bawah
        "template_id": tmpl_id,
        "_use_engine": True,   # preview client memakai RENDERER YANG SAMA dengan generator
        "couple_name": couple,
        "groom_name": data.get("groom_name", ""), "bride_name": data.get("bride_name", ""),
        "groom_insta": data.get("groom_insta", ""), "bride_insta": data.get("bride_insta", ""),
        "event_date": data.get("reception_date", ""), "event_time": data.get("event_time", ""),
        "akkad_date": data.get("akkad_date", ""), "reception_date": data.get("reception_date", ""),
        "venue_name": data.get("venue_name", ""), "venue_address": data.get("venue_address", ""),
        "maps_url": data.get("maps_url", ""), "photo_url": photo_url,
        "whatsapp": data.get("whatsapp", ""), "message": data.get("message", ""),
        "code": "(belum dibuat)", "slug": slugify(couple) or "undangan",
    }
    rendered = render_invitation_html(html_code, o)
    preview_frame = """
    <div class="section-title">Langkah 5 &mdash; Preview</div>
    <div class="order-card" style="padding:12px;">
      <h3 style="margin-bottom:6px;">Begini tampilan undanganmu 🎀</h3>
      <p style="font-size:11px;color:#a1a1aa;margin-bottom:10px;">Template: <b style="color:#fff;">%(tname)s</b> &bull; %(tprice)s. Cek detail di bawah, lalu lanjut kirim pesanan.</p>
      <iframe srcdoc="%(srcdoc)s" style="width:100%%;height:420px;border:1px solid #3f3f46;border-radius:10px;background:#fff;"></iframe>
      <a href="#" onclick="var f=document.querySelector('iframe');window.open('').document.write(f.getAttribute('srcdoc'));return false;" class="btn-secondary" style="font-size:11px;">Buka Preview Fullscreen</a>
    </div>
    <form action="/submit-order" method="POST" class="order-card" style="padding:14px;">
      <h3>Konfirmasi Data</h3>
      %(fields)s
      <button type="submit" class="btn-primary"><i class="fa-solid fa-paper-plane"></i> [6] Kirim Pesanan &rarr;</button>
      <p style="font-size:10px;color:#71717a;margin-top:8px;">Dengan mengirim pesanan Anda setuju melanjutkan ke tahap pembayaran sesuai nominal yang tertera.</p>
    </form>
    """ % dict(tname=_escape(tname), tprice=_escape(tprice), srcdoc=_escape(rendered),
               fields="".join('<input type="hidden" name="%s" value="%s">' % (k, _escape(v))
                              for k, v in [("template_id", tmpl_id), ("photo_url", photo_url)] +
                              [(f, data.get(f, "")) for f in
                               ["couple_name", "groom_name", "bride_name", "groom_insta", "bride_insta",
                                "akkad_date", "reception_date", "event_time", "venue_name",
                                "venue_address", "maps_url", "whatsapp", "message"]]))
    conn.close()
    return web.Response(text=client_page("Preview Undangan", 5, preview_frame), content_type="text/html")

async def handle_submit_order(request):
    """[6] Kirim Pesanan -> Order dibuat (kode unik + slug subdomain) -> [7] Pembayaran."""
    data = await request.post()
    couple = (data.get("couple_name") or "").strip()
    tmpl_id = data.get("template_id")
    if not couple or not tmpl_id:
        raise web.HTTPFound("/start")
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('SELECT t.price, t.duration, p.id, p.name FROM templates t JOIN packages p ON t.package_id=p.id WHERE t.id=?', (tmpl_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise web.HTTPFound("/start")
    tprice, tdur, pid, pname = row
    slug = unique_slug(cur, slugify(couple))
    # kode unik + anti duplikat
    while True:
        code = make_order_code()
        if not cur.execute('SELECT 1 FROM orders WHERE code=?', (code,)).fetchone():
            break
    cur.execute('''INSERT INTO orders (code, slug, template_id, package_id, couple_name, groom_name, bride_name,
                 groom_insta, bride_insta, event_date, event_time, akkad_date, reception_date, venue_name,
                 venue_address, maps_url, photo_url, whatsapp, message, status, amount)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending_payment',?)''',
                (code, slug, tmpl_id, pid, couple, data.get("groom_name", ""), data.get("bride_name", ""),
                 data.get("groom_insta", ""), data.get("bride_insta", ""), data.get("reception_date", ""),
                 data.get("event_time", ""), data.get("akkad_date", ""), data.get("reception_date", ""),
                 data.get("venue_name", ""), data.get("venue_address", ""), data.get("maps_url", ""),
                 data.get("photo_url", ""), data.get("whatsapp", ""), data.get("message", ""), tprice))
    oid = cur.lastrowid
    add_order_event(cur, oid, "pending_payment", "Pesanan dibuat oleh client")
    # ---- Template Engine V1 (additive): wedding draft + payment state ----
    try:
        conn_v1 = conn  # pakai koneksi yang sama sebelum commit
        template_service.ensure_payment_state(conn_v1, oid, tprice)
        w = wedding_service.get_wedding_by_order(conn_v1, oid)
        if w is None:
            wedding_service.create_wedding(
                conn_v1, oid,
                groom_name=data.get("groom_name", ""), bride_name=data.get("bride_name", ""),
                event_date=data.get("reception_date", ""), event_time=data.get("event_time", ""),
                venue=data.get("venue_name", ""), address=data.get("venue_address", ""),
                couple_photo=data.get("photo_url", ""), message=data.get("message", ""))
        conn.commit()
    except Exception as _e:
        print("[v1] wedding/payment_state backfill gagal (order legacy tetap dibuat):", _e)
    conn.close()
    raise web.HTTPFound("/payment?code=" + urlquote(code))

STATUS_STEP = {"pending_payment": 7, "awaiting_verification": 9, "rejected_payment": 8,
               "verified": 10, "processing": 10, "active": 11, "expired": 13}

def payment_instruction_html(o):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    account = get_setting(cur, "payment_account", "BCA 1234567890 a.n SUKA MOTO")
    qris = get_setting(cur, "payment_qris", "QRIS 085156918852 a.n SUKA MOTO")
    conn.close()
    amt_num = parse_amount_rupiah(o["amount"])
    cents = (int(o["id"]) * 7) % 87 + 3  # unik per order utk memudahkan identifikasi transfer
    total = "%s%03d" % (("{:,}".format(amt_num).replace(",", ".")) if amt_num else "-", cents)
    return """
    <div class="order-card" style="border-left:3px solid #34d399;">
      <h3>[7] Instruksi Pembayaran</h3>
      <div class="o-row"><span>Nominal Transfer</span><strong style="color:#34d399;font-size:15px;">Rp %(total)s</strong></div>
      <div class="o-row"><span>Rekening Bank</span><strong>%(acc)s</strong></div>
      <div class="o-row"><span>QRIS / E-Wallet</span><strong>%(qris)s</strong></div>
      <div style="background:#121215;border:1px dashed #fbbf24;border-radius:8px;padding:10px;margin-top:8px;font-size:11px;color:#fcd34d;">
        3 digit akhir <b>%(total)s</b> adalah kode unik dari sistem. Tulis juga kode pesanan
        <b>%(code)s</b> pada berita transfer agar cepat diverifikasi.
      </div>
    </div>""" % dict(total=_escape(total), acc=_escape(account), qris=_escape(qris), code=_escape(o["code"]))

async def handle_payment(request):
    """[7] Halaman pembayaran + instruksi."""
    code = request.query.get("code", "")
    o = fetch_order(sqlite3.connect(DB_NAME), code=code)
    if not o:
        raise web.HTTPFound("/track")
    if o["status"] != "pending_payment":
        raise web.HTTPFound("/track?code=" + urlquote(o["code"]))
    body = """
    <div class="section-title">Langkah 7 &mdash; Pembayaran</div>
    %s
    %s
    <a href="/upload-proof?code=%s" class="btn-primary"><i class="fa-solid fa-upload"></i> [8] Sudah Bayar? Upload Bukti Transfer &rarr;</a>
    <a href="/track?code=%s" class="btn-secondary">Lihat Status Pesanan</a>
    """ % (order_status_card(o), payment_instruction_html(o), urlquote(o["code"]), urlquote(o["code"]))
    return web.Response(text=client_page("Pembayaran", 7, body), content_type="text/html")

async def handle_upload_proof(request):
    """[8] Upload bukti pembayaran -> status awaiting_verification."""
    code = request.query.get("code", "")
    o = fetch_order(sqlite3.connect(DB_NAME), code=code)
    if not o:
        raise web.HTTPFound("/track")
    if o["status"] not in ("pending_payment", "rejected_payment"):
        raise web.HTTPFound("/track?code=" + urlquote(o["code"]))
    note = ""
    if o["status"] == "rejected_payment":
        note = '<div style="background:#7f1d1d;color:#fecaca;font-size:11px;padding:10px;border-radius:8px;margin-bottom:12px;">&#10060; Bukti sebelumnya ditolak: %s. Silakan upload ulang.</div>' % _escape(o["reject_reason"] or "data transfer tidak cocok")
    body = """
    <div class="section-title">Langkah 8 &mdash; Upload Bukti Pembayaran</div>
    %s%s
    <form action="/upload-proof?code=%s" method="POST" enctype="multipart/form-data" class="order-card">
      <h3>Bukti Transfer</h3>
      <label class="fld">Foto/Screenshot bukti transfer (jpg/png/webp/pdf, maks 5 MB) *</label>
      <input type="file" name="proof" required accept=".jpg,.jpeg,.png,.webp,.gif,.pdf" style="background:#121215;padding:6px;">
      <label class="fld">Nama Pengirim Transfer (opsional)</label>
      <input type="text" name="sender" placeholder="Sesuai rekening pengirim">
      <button type="submit" class="btn-primary"><i class="fa-solid fa-circle-check"></i> Kirim Bukti &amp; Menunggu Verifikasi</button>
    </form>
    """ % (note, order_status_card(o), urlquote(o["code"]))
    return web.Response(text=client_page("Upload Bukti Pembayaran", 8, body), content_type="text/html")

async def handle_upload_proof_post(request):
    code = request.query.get("code", "")
    o = fetch_order(sqlite3.connect(DB_NAME), code=code)
    if not o:
        raise web.HTTPFound("/track")
    if o["status"] not in ("pending_payment", "rejected_payment"):
        raise web.HTTPFound("/track?code=" + urlquote(o["code"]))
    reader = await request.multipart()
    field = await reader.next()
    saved_name, sender = None, ""
    while field is not None:
        if field.name == "proof" and field.filename:
            original = os.path.basename(field.filename)
            ext = os.path.splitext(original)[1].lower()
            declared = (field.headers.get("Content-Type") if field.headers else "").split(";")[0].strip().lower()
            guessed = mimetypes.guess_type(original)[0] or ""
            if ext in ALLOWED_UPLOAD_EXTS and (not declared or declared in ALLOWED_UPLOAD_MIMES) \
                    and (not guessed or guessed in ALLOWED_UPLOAD_MIMES):
                safe_stem = re.sub(r"[^A-Za-z0-9_-]", "_", os.path.splitext(original)[0])[:40] or "bukti"
                cand = "bukti_%s_%d%s" % (safe_stem, int(time.time() * 1000), ext)
                fpath = os.path.join(UPLOAD_DIR, cand)
                size, oversize = 0, False
                with open(fpath, "wb") as f:
                    while True:
                        chunk = await field.read_chunk()
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_UPLOAD_BYTES:
                            oversize = True
                            break
                        f.write(chunk)
                if oversize or size == 0:
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass
                else:
                    saved_name = cand
        elif field.name == "sender":
            sender = ((await field.read(decode=True)) or b"").decode("utf-8", "replace")[:60]
        field = await reader.next()
    if not saved_name:
        raise web.HTTPFound("/upload-proof?code=" + urlquote(o["code"]) + "&err=file")
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('UPDATE orders SET proof_filename = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (saved_name, "awaiting_verification", o["id"]))
    add_order_event(cur, o["id"], "awaiting_verification", "Bukti bayar diupload oleh client. Pengirim: " + sender)
    conn.commit()
    conn.close()
    raise web.HTTPFound("/track?code=" + urlquote(o["code"]))

async def handle_track(request):
    """Halaman status pesanan client ([7]-[13]) berdasarkan kode / daftar via WA."""
    refresh_expired_orders()
    code = request.query.get("code", "").upper()
    err = request.query.get("err", "")
    if not code:
        body = """
        <div class="section-title">Cek Status Pesanan</div>
        <div class="order-card">
          <h3>Masukkan Kode Pembayaran</h3>
          <p style="font-size:11px;color:#a1a1aa;margin-bottom:8px;">Kode unik dikirim saat pesanan dibuat, contoh: SKT-A7K2MQ</p>
          <form action="/track" method="GET">
            <input type="text" name="code" placeholder="SKT-XXXXXX" required style="text-transform:uppercase;">
            <button type="submit" class="btn-primary">Cek Status</button>
          </form>
        </div>
        """
        if err == "notfound":
            body = '<div style="background:#7f1d1d;color:#fecaca;font-size:12px;padding:10px;border-radius:8px;margin-bottom:12px;">Kode pesanan tidak ditemukan.</div>' + body
        return web.Response(text=client_page("Status Pesanan", 0, body), content_type="text/html")
    o = fetch_order(sqlite3.connect(DB_NAME), code=code)
    if not o:
        raise web.HTTPFound("/track?err=notfound")
    step = STATUS_STEP.get(o["status"], 7)
    actions = ""
    if o["status"] == "pending_payment":
        actions += '<a href="/payment?code=%s" class="btn-primary">Lihat Instruksi Pembayaran &rarr;</a>' % urlquote(o["code"])
    if o["status"] in ("pending_payment", "rejected_payment"):
        actions += '<a href="/upload-proof?code=%s" class="btn-secondary"><i class="fa-solid fa-upload"></i> Upload / Perbaiki Bukti Pembayaran</a>' % urlquote(o["code"])
    if o["status"] == "active":
        wa_guest = "https://wa.me/?text=" + urlquote(
            "Undangan Pernikahan %s\n%s" % (o["couple_name"], invite_url_for(o["slug"])))
        actions += ('<a href="%s" target="_blank" class="btn-wa"><i class="fa-brands fa-whatsapp"></i> Bagikan Link Undangan via WhatsApp</a>'
                    '<button class="btn-secondary" onclick="navigator.clipboard.writeText(\'%s\');alert(\'Link disalin!\');">Salin Link Undangan</button>'
                    % (wa_guest, _escape(invite_url_for(o["slug"]))))
    if o["status"] == "expired":
        admin_wa = "6285156918852"
        actions += '<a href="https://wa.me/%s?text=%s" class="btn-wa"><i class="fa-brands fa-whatsapp"></i> Hubungi Admin untuk Perpanjangan</a>' % (
            admin_wa, urlquote("Halo admin, undangan %s (%s) sudah EXPIRED. Saya ingin memperpanjang paket." % (o["couple_name"], o["code"])))
    body = """
    <div class="section-title">Status Pesanan Anda</div>
    %s%s
    <a href="/" class="btn-secondary">Kembali ke Beranda</a>
    """ % (order_status_card(o), actions)
    return web.Response(text=client_page("Status Pesanan", step, body), content_type="text/html")

async def handle_my_orders(request):
    """Daftar pesanan via nomor WhatsApp."""
    phone = (request.query.get("phone") or "").strip()
    list_html = ""
    if phone:
        conn = sqlite3.connect(DB_NAME)
        rows = conn.execute('SELECT code, slug, couple_name, amount, status, expires_at FROM orders WHERE whatsapp LIKE ? ORDER BY id DESC LIMIT 20',
                            ("%" + re.sub(r"\D", "", phone)[-8:] + "%",)).fetchall()
        conn.close()
        if rows:
            for r in rows:
                pill = '<span class="status-pill st-%s">%s</span>' % (r[4], _escape(ORDER_STATUS_LABEL.get(r[4], r[4])))
                list_html += ('<a href="/track?code=%s" style="display:block;text-decoration:none;">'
                              '<div class="o-row"><span>%s<br><small style="color:#71717a;">%s</small></span><strong>%s</strong></div></a>') % (
                              urlquote(r[0]), _escape(r[2]), _escape(r[0]), pill)
        else:
            list_html = '<p style="font-size:11px;color:#71717a;text-align:center;padding:15px;">Belum ada pesanan untuk nomor ini.</p>'
    body = """
    <div class="section-title">Pesanan Saya</div>
    <div class="order-card">
      <h3>Cari dengan Nomor WhatsApp</h3>
      <form action="/my-orders" method="GET">
        <input type="tel" name="phone" placeholder="08123456789" required value="%s">
        <button type="submit" class="btn-primary">Tampilkan Pesanan</button>
      </form>
    </div>
    <div class="order-card">%s</div>
    """ % (_escape(phone), list_html or '<p style="font-size:11px;color:#71717a;text-align:center;padding:10px;">Masukkan nomor WhatsApp yang dipakai saat memesan.</p>')
    return web.Response(text=client_page("Pesanan Saya", 0, body), content_type="text/html")

async def handle_invite_subdomain(request):
    """Melayani https://[nama-pasangan].invite.sukamoto.web.id (via wildcard DNS+TLS).
    Jika subdomain belum mengarah ke app, tetap tersedia fallback /u/<slug>."""
    refresh_expired_orders()
    host = (request.headers.get("Host") or "").split(":")[0].lower()
    slug = ""
    if host.endswith("." + INVITE_DOMAIN.lower()):
        slug = host[: -len(INVITE_DOMAIN) - 1]
    if not slug:
        slug = request.match_info.get("slug", "")
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE slug = ?", (slug,))
    row = cur.fetchone()
    o = dict(zip([c[0] for c in cur.description], row)) if row else None
    html_out = None
    if o:
        if o["status"] in ("active",):
            # Prioritas 1: hasil GENERATED (index.html) dari Template Engine V1.
            gen_index = os.path.join(GENERATED_ROOT, slug, "index.html")
            if os.path.isfile(gen_index):
                with open(gen_index, "r", encoding="utf-8") as f:
                    html_out = f.read()
            else:
                # jalur lama tetap berfungsi untuk order legacy existing
                o["_use_engine"] = True   # placeholder template dirender via engine V1
                cur.execute("SELECT html_code FROM templates WHERE id = ?", (o["template_id"],))
                trow = cur.fetchone()
                html_out = render_invitation_html(trow[0] if trow else "", o)
        elif o["status"] == "expired":
            html_out = _closed_page(o["couple_name"], "Masa aktif undangan ini sudah berakhir (EXPIRED).", o)
        else:
            html_out = _closed_page(o["couple_name"], "Undangan ini masih dalam proses pembuatan oleh tim SUKA MOTO. Mohon tunggu konfirmasi dari pemilik acara.", o)
    else:
        html_out = _closed_page("Undangan", "Link undangan tidak ditemukan / belum aktif.", None)
    conn.close()
    return web.Response(text=html_out, content_type="text/html")

def _closed_page(couple, reason, o):
    countdown = ""
    if o and o.get("event_date"):
        try:
            d = _dt.date.fromisoformat(o["event_date"]) - _dt.date.today()
            if d.days > 0:
                countdown = "<p style='color:#a1a1aa;font-size:13px;'>Menuju hari Bahagia: <b style='color:#fbbf24;'>%d hari</b></p>" % d.days
        except Exception:
            pass
    return """<!DOCTYPE html><html lang="id"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Undangan %s</title>
<link href="https://fonts.googleapis.com/css2?family=Alex+Brush&family=Plus+Jakarta+Sans:wght@400;600;800&display=swap" rel="stylesheet">
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#09090b;color:#f4f4f5;font-family:'Plus Jakarta Sans',sans-serif;text-align:center;}
.c{max-width:380px;padding:40px 24px;background:#121215;border:1px solid #27272a;border-radius:18px;}
.script{font-family:'Alex Brush',cursive;font-size:40px;color:#fbbf24;}
.btn{display:inline-block;margin-top:18px;background:#22c55e;color:#fff;font-weight:700;padding:10px 20px;border-radius:8px;text-decoration:none;font-size:13px;}</style></head>
<body><div class="c"><div class="script">The Wedding of</div><h1 style="font-size:20px;margin:6px 0 14px;">%s</h1>
<div style="font-size:40px;">&#127886;</div>%s
<p style="font-size:13px;color:#d4d4d8;line-height:1.6;margin-top:12px;">%s</p>
<a class="btn" href="https://%s"><i class="fa-solid fa-heart"></i> Info &amp; Pembuatan Undangan</a>
<p style="font-size:10px;color:#71717a;margin-top:16px;">Powered by SUKA MOTO Invitation</p></div></body></html>""" % (
        _escape(couple), _escape(couple), countdown, _escape(reason), _escape(INVITE_DOMAIN))

LOGIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Login Admin - SUKA MOTO</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;font-family:'Plus Jakarta Sans',sans-serif}
body{background:#09090b;color:#f4f4f5;display:flex;justify-content:center;align-items:center;min-height:100vh;padding:20px}
.card{width:100%;max-width:360px;background:#121215;border:1px solid #27272a;border-radius:14px;padding:28px}
.brand-main{font-size:16px;font-weight:800;color:#fff;letter-spacing:1px;text-align:center}
.brand-sub{font-size:13px;color:#fbbf24;text-align:center;margin-bottom:20px}
.err{background:#7f1d1d;color:#fecaca;font-size:12px;padding:10px 12px;border-radius:8px;margin-bottom:14px}
label{font-size:11px;color:#a1a1aa;display:block;margin-top:10px}
input{width:100%;padding:11px 12px;margin-top:4px;border-radius:8px;border:1px solid #3f3f46;background:#18181b;color:#fff;font-size:14px}
button{width:100%;margin-top:18px;padding:12px;border:none;border-radius:8px;background:#fbbf24;color:#000;font-weight:800;font-size:14px;cursor:pointer}
.back{display:block;text-align:center;margin-top:16px;font-size:11px;color:#71717a;text-decoration:none}
.lock{text-align:center;font-size:26px;color:#fbbf24;margin-bottom:10px}
</style>
</head>
<body>
<div class="card">
<div class="lock">&#128274;</div>
<div class="brand-main">SUKA MOTO</div>
<div class="brand-sub">Admin Control &mdash; akses terbatas</div>
__ERROR_BLOCK__
<form action="/admin/login" method="POST" autocomplete="off">
<label>Username</label>
<input type="text" name="username" required maxlength="50">
<label>Password</label>
<input type="password" name="password" required maxlength="200">
<button type="submit">Masuk</button>
</form>
<a href="/" class="back">&larr; Kembali ke Beranda</a>
</div>
</body>
</html>"""

async def handle_admin_login(request):
    error = request.query.get('error', '')
    msg_map = {
        'invalid': 'Username atau password salah.',
        'rate': 'Terlalu banyak percobaan gagal. Coba lagi dalam 15 menit.',
        'inactive': 'Akun dinonaktifkan.',
    }
    err_html = ('<div class="err">%s</div>' % _escape(msg_map.get(error, 'Kredensial tidak valid.'))) if error else ''
    page = LOGIN_PAGE_HTML.replace('__ERROR_BLOCK__', err_html)
    return web.Response(text=page, content_type='text/html')

async def handle_admin_login_post(request):
    ip = request.remote or 'unknown'
    if login_rate_limited(ip):
        raise web.HTTPFound('/admin/login?error=rate')
    data = await request.post()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT id, username, password_hash, is_active FROM admin_users WHERE username = ?', (username,))
    row = cursor.fetchone()
    ok = bool(row) and row[3] == 1 and verify_password(password, row[2])
    if ok:
        clear_login_attempts(ip)
        cursor.execute('UPDATE admin_users SET last_login = CURRENT_TIMESTAMP WHERE id = ?', (row[0],))
        conn.commit()
        conn.close()
        resp = web.HTTPFound('/admin')
        resp.set_cookie(
            'admin_session',
            make_session_token(row[0], row[1]),
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite='Strict',
            secure=not ALLOW_INSECURE_COOKIE,
            path='/',
        )
        raise resp
    conn.close()
    record_login_attempt(ip)
    raise web.HTTPFound('/admin/login?error=invalid')

async def handle_admin_logout(request):
    resp = web.HTTPFound('/admin/login')
    resp.del_cookie('admin_session', path='/')
    raise resp

async def handle_index(request):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('SELECT id, name, price, discount, duration, image_url FROM templates WHERE is_top10 = 1 LIMIT 10')
    top10 = cursor.fetchall()

    cursor.execute('SELECT id, name, subtitle, image_url FROM packages')
    pkgs = cursor.fetchall()
    conn.close()

    # Baca langsung dari file fisik homepage.html
    if os.path.exists(HOMEPAGE_FILE):
        with open(HOMEPAGE_FILE, "r", encoding="utf-8") as f:
            custom_homepage_html = f.read()
    else:
        custom_homepage_html = "<p>File homepage.html tidak ditemukan.</p>"

    top10_html = ""
    for tid, tname, tprice, tdisc, tdur, timg in top10:
        disc_badge = f'<span style="position:absolute; top:6px; left:6px; background:#ef4444; color:#fff; font-size:8px; font-weight:bold; padding:2px 5px; border-radius:3px;">{tdisc}</span>' if tdisc else ''
        top10_html += f"""
        <div style="min-width: 130px; background: #18181b; border: 1px solid #27272a; border-radius: 10px; overflow: hidden; display: flex; flex-direction: column; justify-content: space-between;">
            <div style="height: 90px; position: relative;">
                {disc_badge}
                <img src="{timg}" alt="{tname}" style="width:100%; height:100%; object-fit:cover;">
            </div>
            <div style="padding: 8px;">
                <div style="font-size: 11px; font-weight: bold; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{tname}</div>
                <div style="font-size: 10px; color: #34d399; font-weight: bold; margin-bottom: 6px;">{tprice}</div>
                <a href="/template-action?id={tid}" style="display:block; background:#27272a; color:#fbbf24; text-align:center; padding:4px; border-radius:4px; font-size:9px; font-weight:bold; text-decoration:none;">Pilih</a>
            </div>
        </div>
        """

    if not top10_html:
        top10_html = "<p style='font-size:11px; color:#71717a;'>Belum ada template unggulan.</p>"

    pkg_grid_html = ""
    hamburger_folder_html = """
        <a href="/"><i class="fa-solid fa-house" style="color:#fbbf24;"></i> Home</a>
        <div style="padding: 6px 12px; font-size: 9px; font-weight: bold; color: #71717a; text-transform: uppercase; border-top:1px solid #27272a; border-bottom:1px solid #27272a; margin-top:4px;">Kategori Paket</div>
    """
    for pid, name, sub, img_url in pkgs:
        pkg_grid_html += f"""
        <a href="/templates?package_id={pid}" style="background: #18181b; border: 1px solid #27272a; border-radius: 12px; overflow: hidden; text-decoration: none; display: flex; flex-direction: column; justify-content: space-between; transition: 0.2s;">
            <div style="height: 100px; width: 100%;">
                <img src="{img_url}" alt="{name}" style="width:100%; height:100%; object-fit:cover;">
            </div>
            <div style="padding: 10px;">
                <h3 style="font-size: 12px; font-weight: 700; color: #fff; margin-bottom: 2px;">{name}</h3>
                <p style="font-size: 10px; color: #a1a1aa; margin-bottom: 8px; line-height: 1.2;">{sub}</p>
                <div style="font-size: 10px; color: #fbbf24; font-weight: bold; display: flex; align-items: center; justify-content: space-between;">
                    <span>Lihat Koleksi</span>
                    <i class="fa-solid fa-arrow-right"></i>
                </div>
            </div>
        </a>
        """
        hamburger_folder_html += f'<a href="/templates?package_id={pid}"><i class="fa-solid fa-folder" style="color:#fbbf24;"></i> {name}</a>'

    html_content = f"""
    <!DOCTYPE html>
    <html lang="id">
    <head>
        {BASE_HEAD}
        <style>
            .bio-card {{ background: linear-gradient(135deg, #18181b 0%, #121215 100%); border: 1px solid #27272a; border-radius: 14px; padding: 18px; margin-bottom: 25px; text-align: center; }}
            .bio-card h4 {{ color: #fbbf24; font-size: 13px; font-weight: 700; margin-bottom: 6px; }}
            .bio-card p {{ font-size: 11px; color: #a1a1aa; line-height: 1.5; margin-bottom: 12px; }}
            .quote {{ font-style: italic; font-size: 11px; color: #d4d4d8; border-top: 1px solid #27272a; padding-top: 10px; }}
            .packages-grid-2col {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }}
            .top10-scroll {{ display: flex; gap: 10px; overflow-x: auto; padding-bottom: 10px; scrollbar-width: thin; }}
            .top10-scroll::-webkit-scrollbar {{ height: 4px; }}
            .top10-scroll::-webkit-scrollbar-thumb {{ background: #27272a; border-radius: 4px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="content-wrap">
                <div class="navbar">
                    <a href="/" class="brand-group">
                        <span class="brand-main">SUKA MOTO</span>
                        <span class="brand-sub">Invitation</span>
                    </a>
                    <div class="nav-actions">
                        <button class="hamburger-btn" onclick="toggleMenu()"><i class="fa-solid fa-bars"></i></button>
                        <div id="menuDropdown" class="menu-dropdown">
                            {hamburger_folder_html}
                        </div>
                    </div>
                </div>

                {custom_homepage_html}

                <div class="section-title">⭐ 10 Template Terbaik & Unggulan</div>
                <div class="top10-scroll">
                    {top10_html}
                </div>

                <div class="section-title" style="margin-top: 25px;">Pilihan Kategori Paket</div>
                <div class="packages-grid-2col">
                    {pkg_grid_html}
                </div>
            </div>

            {FOOTER_HTML}
        </div>
        {ADMIN_SECURITY_SCRIPT}
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type='text/html')

async def handle_templates(request):
    pkg_id = request.query.get('package_id', '1')
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM packages WHERE id = ?', (pkg_id,))
    pkg_res = cursor.fetchone()
    pkg_name = pkg_res[0] if pkg_res else "Koleksi Paket"

    cursor.execute('SELECT id, name, price, discount, duration, image_url FROM templates WHERE package_id = ?', (pkg_id,))
    tmpls = cursor.fetchall()

    cursor.execute('SELECT id, name FROM packages')
    pkgs = cursor.fetchall()
    conn.close()

    hamburger_folder_html = """
        <a href="/"><i class="fa-solid fa-house" style="color:#fbbf24;"></i> Home</a>
        <div style="padding: 6px 12px; font-size: 9px; font-weight: bold; color: #71717a; text-transform: uppercase; border-top:1px solid #27272a; border-bottom:1px solid #27272a; margin-top:4px;">Kategori Paket</div>
    """
    for pid, name in pkgs:
        hamburger_folder_html += f'<a href="/templates?package_id={pid}"><i class="fa-solid fa-folder" style="color:#fbbf24;"></i> {name}</a>'

    grid_html = ""
    for tid, tname, tprice, tdisc, tdur, timg in tmpls:
        disc_badge = f'<span class="badge-disc">{tdisc}</span>' if tdisc else ''
        dur_text = f'<span class="tmpl-dur"><i class="fa-regular fa-clock"></i> {tdur}</span>' if tdur else ''
        grid_html += f"""
        <div class="tmpl-card">
            <div class="tmpl-img">
                {disc_badge}
                <img src="{timg}" alt="{tname}">
            </div>
            <div class="tmpl-info">
                <div>
                    <h4>{tname}</h4>
                    <span class="tmpl-price">{tprice}</span>
                    {dur_text}
                </div>
                <a href="/template-action?id={tid}" class="preview-btn">Pilih Template</a>
            </div>
        </div>
        """

    if not grid_html:
        grid_html = "<p style='grid-column: span 2; text-align:center; color:#71717a; font-size:12px; padding:30px;'>Belum ada template di paket ini.</p>"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="id">
    <head>
        {BASE_HEAD}
        <style>
            .back-link {{ font-size: 11px; color: #fbbf24; text-decoration: none; display: inline-flex; align-items: center; gap: 6px; margin-bottom: 15px; font-weight: bold; }}
            .page-heading {{ margin-bottom: 20px; }}
            .page-heading h2 {{ font-size: 16px; color: #fff; font-weight: 700; }}
            .page-heading p {{ font-size: 11px; color: #a1a1aa; }}
            .template-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }}
            .tmpl-card {{ background: #18181b; border: 1px solid #27272a; border-radius: 12px; overflow: hidden; display: flex; flex-direction: column; justify-content: space-between; }}
            .tmpl-img {{ width: 100%; height: 110px; overflow: hidden; position: relative; }}
            .tmpl-img img {{ width: 100%; height: 100%; object-fit: cover; }}
            .badge-disc {{ position: absolute; top: 8px; left: 8px; background: #ef4444; color: #fff; font-size: 9px; font-weight: bold; padding: 2px 6px; border-radius: 4px; }}
            .tmpl-info {{ padding: 10px; display: flex; flex-direction: column; flex-grow: 1; justify-content: space-between; }}
            .tmpl-info h4 {{ font-size: 12px; color: #fff; font-weight: 600; margin-bottom: 2px; line-height: 1.3; }}
            .tmpl-price {{ font-size: 11px; color: #34d399; font-weight: bold; display: block; margin-bottom: 4px; }}
            .tmpl-dur {{ font-size: 10px; color: #a1a1aa; display: block; margin-bottom: 8px; }}
            .preview-btn {{ background: #27272a; color: #fbbf24; text-align: center; padding: 6px; border-radius: 6px; text-decoration: none; font-size: 10px; font-weight: bold; transition: background 0.2s; display: block; }}
            .preview-btn:hover {{ background: #fbbf24; color: #000; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="content-wrap">
                <div class="navbar">
                    <a href="/" class="brand-group">
                        <span class="brand-main">SUKA MOTO</span>
                        <span class="brand-sub">Invitation</span>
                    </a>
                    <div class="nav-actions">
                        <button class="hamburger-btn" onclick="toggleMenu()"><i class="fa-solid fa-bars"></i></button>
                        <div id="menuDropdown" class="menu-dropdown">
                            {hamburger_folder_html}
                        </div>
                    </div>
                </div>

                <a href="/" class="back-link"><i class="fa-solid fa-arrow-left"></i> Kembali ke Beranda</a>
                
                <div class="page-heading">
                    <h2>{pkg_name}</h2>
                    <p>Pilih template eksklusif sesuai tema pernikahan Anda</p>
                </div>

                <div class="template-grid">
                    {grid_html}
                </div>
            </div>

            {FOOTER_HTML}
        </div>
        {ADMIN_SECURITY_SCRIPT}
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type='text/html')

async def handle_template_action(request):
    tmpl_id = request.query.get('id')
    if not tmpl_id:
        return web.Response(text="Template tidak ditemukan", status=404)
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT name, price, discount, duration, image_url FROM templates WHERE id = ?', (tmpl_id,))
    tmpl = cursor.fetchone()
    
    cursor.execute('SELECT id, name FROM packages')
    pkgs = cursor.fetchall()
    conn.close()
    
    if not tmpl:
        return web.Response(text="Template tidak terdaftar", status=404)
        
    tname, tprice, tdisc, tdur, timg = tmpl
    disc_html = f'<div style="color:#ef4444; font-size:11px; font-weight:bold; margin-bottom:5px;">{tdisc}</div>' if tdisc else ''
    
    hamburger_folder_html = """
        <a href="/"><i class="fa-solid fa-house" style="color:#fbbf24;"></i> Home</a>
        <div style="padding: 6px 12px; font-size: 9px; font-weight: bold; color: #71717a; text-transform: uppercase; border-top:1px solid #27272a; border-bottom:1px solid #27272a; margin-top:4px;">Kategori Paket</div>
    """
    for pid, name in pkgs:
        hamburger_folder_html += f'<a href="/templates?package_id={pid}"><i class="fa-solid fa-folder" style="color:#fbbf24;"></i> {name}</a>'

    html_content = f"""
    <!DOCTYPE html>
    <html lang="id">
    <head>
        {BASE_HEAD}
        <style>
            .action-card {{ background: #18181b; border: 1px solid #27272a; border-radius: 14px; padding: 20px; text-align: center; margin-top: 15px; }}
            .action-img {{ width: 100%; height: 160px; border-radius: 8px; overflow: hidden; margin-bottom: 15px; }}
            .action-img img {{ width: 100%; height: 100%; object-fit: cover; }}
            .btn-demo {{ display: block; background: #27272a; color: #fff; padding: 12px; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 12px; margin-bottom: 10px; border: 1px solid #3f3f46; transition: 0.2s; }}
            .btn-demo:hover {{ background: #3f3f46; }}
            .btn-build {{ display: block; background: #fbbf24; color: #000; padding: 12px; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 12px; transition: 0.2s; }}
            .btn-build:hover {{ background: #f59e0b; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="content-wrap">
                <div class="navbar">
                    <a href="/" class="brand-group">
                        <span class="brand-main">SUKA MOTO</span>
                        <span class="brand-sub">Invitation</span>
                    </a>
                    <div class="nav-actions">
                        <button class="hamburger-btn" onclick="toggleMenu()"><i class="fa-solid fa-bars"></i></button>
                        <div id="menuDropdown" class="menu-dropdown">
                            {hamburger_folder_html}
                        </div>
                    </div>
                </div>

                <a href="/" style="font-size:11px; color:#fbbf24; text-decoration:none; font-weight:bold;"><i class="fa-solid fa-arrow-left"></i> Kembali</a>

                <div class="action-card">
                    <div class="action-img">
                        <img src="{timg}" alt="{tname}">
                    </div>
                    <h2 style="font-size: 16px; color: #fff; margin-bottom: 4px;">{tname}</h2>
                    {disc_html}
                    <div style="font-size: 14px; color: #34d399; font-weight: bold; margin-bottom: 4px;">{tprice}</div>
                    <div style="font-size: 11px; color: #a1a1aa; margin-bottom: 20px;"><i class="fa-regular fa-clock"></i> {tdur}</div>

                    <a href="/demo?id={tmpl_id}" class="btn-demo" target="_blank"><i class="fa-solid fa-eye"></i> Lihat Demo Template</a>
                    <a href="/form?template_id={tmpl_id}" class="btn-build"><i class="fa-solid fa-wand-magic-sparkles"></i> Buat Undangan Ini</a>
                </div>
            </div>
            {FOOTER_HTML}
        </div>
        {ADMIN_SECURITY_SCRIPT}
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type='text/html')

async def handle_demo(request):
    tmpl_id = request.query.get('id')
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT html_code FROM templates WHERE id = ?', (tmpl_id,))
    res = cursor.fetchone()
    conn.close()
    
    if not res or not res[0]:
        return web.Response(text="Demo tidak ditemukan", status=404)
        
    protected_html = res[0].replace("<body>", """<body>
    <script>
        document.addEventListener('contextmenu', event => event.preventDefault());
        document.onkeydown = function(e) {
            if(e.keyCode == 123 || (e.ctrlKey && e.shiftKey && (e.keyCode == 73 || e.keyCode == 74)) || (e.ctrlKey && e.keyCode == 85)) {
                return false;
            }
        }
    </script>
    <div style="position:fixed; bottom:10px; right:10px; background:rgba(0,0,0,0.7); color:#fbbf24; font-size:10px; padding:4px 8px; border-radius:4px; z-index:9999;">SUKA MOTO DEMO MODE</div>
    """)
    return web.Response(text=protected_html, content_type='text/html')

async def handle_editor(request):
    tmpl_id = request.query.get('id')
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT name, price, html_code FROM templates WHERE id = ?', (tmpl_id,))
    res = cursor.fetchone()
    conn.close()
    
    if not res:
        return web.Response(text="Template tidak ditemukan", status=404)
        
    tname, tprice, html_code = res
    editor_banner = f"""
    <div id="sukamoto-builder-bar" style="position:fixed; top:0; left:0; width:100%; background:#18181b; border-bottom:1px solid #27272a; padding:10px 15px; display:flex; justify-content:space-between; align-items:center; z-index:999999; box-shadow:0 4px 6px rgba(0,0,0,0.3);">
        <div style="font-size:12px; color:#fff; font-weight:bold;">Editor: {tname} <span style="color:#34d399; margin-left:8px;">{tprice}</span></div>
        <div>
            <span style="font-size:10px; color:#fbbf24; margin-right:10px;">Klik teks untuk mengedit langsung!</span>
            <a href="/checkout?id={tmpl_id}" style="background:#fbbf24; color:#000; padding:6px 12px; border-radius:6px; font-size:11px; font-weight:bold; text-decoration:none;">Lanjut ke Pembayaran &rarr;</a>
        </div>
    </div>
    <div style="height:50px;"></div>
    """
    modified_html = html_code.replace("<body>", f"<body>{editor_banner}")
    return web.Response(text=modified_html, content_type='text/html')

async def handle_checkout(request):
    tmpl_id = request.query.get('id')
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT name, price, discount, duration FROM templates WHERE id = ?', (tmpl_id,))
    tmpl = cursor.fetchone()
    conn.close()
    
    if not tmpl:
        return web.Response(text="Data checkout tidak valid", status=404)
        
    tname, tprice, tdisc, tdur = tmpl
    html_content = f"""
    <!DOCTYPE html>
    <html lang="id">
    <head>
        {BASE_HEAD}
        <style>
            .checkout-box {{ background: #18181b; border: 1px solid #27272a; border-radius: 12px; padding: 20px; margin-top: 15px; }}
            .summary-row {{ display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 10px; color: #a1a1aa; }}
            .summary-row.total {{ font-size: 14px; color: #34d399; font-weight: bold; border-top: 1px solid #27272a; padding-top: 10px; margin-top: 10px; }}
            input, select {{ padding: 10px; margin: 6px 0; border-radius: 8px; border: 1px solid #3f3f46; background: #121215; color: #fff; width: 100%; font-size: 12px; }}
            .pay-btn {{ display: block; width: 100%; background: #34d399; color: #000; font-weight: bold; padding: 12px; border-radius: 8px; border: none; margin-top: 15px; cursor: pointer; text-align: center; text-decoration: none; font-size: 13px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="content-wrap">
                <div class="navbar">
                    <a href="/" class="brand-group">
                        <span class="brand-main">SUKA MOTO</span>
                        <span class="brand-sub">Invitation</span>
                    </a>
                </div>

                <div class="section-title">Konfirmasi Pembayaran & Pesanan</div>
                
                <div class="checkout-box">
                    <h3 style="font-size: 14px; color: #fff; margin-bottom: 12px;">Ringkasan Paket</h3>
                    <div class="summary-row"><span>Template</span><strong style="color:#fff;">{tname}</strong></div>
                    <div class="summary-row"><span>Durasi Aktif</span><strong style="color:#fff;">{tdur}</strong></div>
                    <div class="summary-row"><span>Promo/Diskon</span><strong style="color:#ef4444;">{tdisc if tdisc else 'Tidak ada'}</strong></div>
                    <div class="summary-row total"><span>Total Pembayaran</span><span>{tprice}</span></div>
                </div>

                <div class="checkout-box" style="margin-top: 15px;">
                    <h3 style="font-size: 14px; color: #fff; margin-bottom: 10px;">Data Pemesan</h3>
                    <form action="/guestbook" method="GET">
                        <input type="hidden" name="id" value="{tmpl_id}">
                        <label style="font-size:11px; color:#a1a1aa;">Nama Lengkap / Mempelai:</label>
                        <input type="text" name="buyer_name" placeholder="Contoh: Rian & Siska" required>
                        <label style="font-size:11px; color:#a1a1aa; margin-top:8px; display:block;">Nomor WhatsApp:</label>
                        <input type="text" name="whatsapp" placeholder="Contoh: 08123456789" required>
                        <label style="font-size:11px; color:#a1a1aa; margin-top:8px; display:block;">Metode Pembayaran:</label>
                        <select name="bank">
                            <option value="BCA">Transfer BCA - 1234567890 a.n SUKA MOTO</option>
                            <option value="DANA">QRIS / DANA - 085156918852</option>
                        </select>
                        <button type="submit" class="pay-btn">Simulasi Bayar & Lanjut ke Buku Tamu &rarr;</button>
                    </form>
                </div>
            </div>
            {FOOTER_HTML}
        </div>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type='text/html')

async def handle_guestbook(request):
    tmpl_id = request.query.get('id')
    buyer_name = request.query.get('buyer_name', 'Mempelai')
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="id">
    <head>
        {BASE_HEAD}
        <style>
            .gb-box {{ background: #18181b; border: 1px solid #27272a; border-radius: 12px; padding: 18px; margin-top: 15px; }}
            textarea {{ width: 100%; height: 90px; background: #121215; border: 1px solid #3f3f46; color: #fff; border-radius: 8px; padding: 10px; font-size: 12px; resize: vertical; }}
            .btn-action {{ background: #fbbf24; color: #000; font-weight: bold; border: none; padding: 10px; border-radius: 8px; width: 100%; cursor: pointer; margin-top: 10px; font-size: 12px; }}
            .guest-item {{ background: #121215; border: 1px solid #27272a; padding: 10px; border-radius: 8px; margin-top: 8px; display: flex; justify-content: space-between; align-items: center; font-size: 11px; }}
            .link-btns {{ display: flex; gap: 5px; }}
            .link-btns a, .link-btns button {{ background: #27272a; color: #fbbf24; border: none; padding: 5px 8px; border-radius: 4px; font-size: 10px; cursor: pointer; text-decoration: none; font-weight: bold; }}
            .link-btns a.wa {{ background: #22c55e; color: #fff; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="content-wrap">
                <div class="navbar">
                    <a href="/" class="brand-group">
                        <span class="brand-main">SUKA MOTO</span>
                        <span class="brand-sub">Invitation</span>
                    </a>
                </div>

                <div class="section-title">Custom Buku Tamu & Generator Link</div>
                
                <div class="gb-box">
                    <h3 style="font-size: 13px; color: #fff; margin-bottom: 6px;">Input Daftar Tamu Undangan</h3>
                    <p style="font-size: 11px; color: #a1a1aa; margin-bottom: 12px;">Masukkan nama-nama tamu (satu nama per baris) agar sistem otomatis membuatkan link personal.</p>
                    <textarea id="guestListInput" placeholder="Bpk. Budi Santoso&#10;Ibu Siti Aminah&#10;Rian & Partner"></textarea>
                    <button type="button" class="btn-action" onclick="generateLinks()">Generate Link Tamu</button>
                </div>

                <div class="gb-box" style="margin-top: 15px;">
                    <h3 style="font-size: 13px; color: #fff; margin-bottom: 10px;">Daftar Link Undangan Spesifik Tamu</h3>
                    <div id="resultContainer" style="max-height: 250px; overflow-y: auto;">
                        <p style="font-size: 11px; color: #71717a; text-align: center; padding: 15px;">Belum ada tamu di-generate. Masukkan nama di atas lalu klik tombol generate.</p>
                    </div>
                </div>
            </div>
            {FOOTER_HTML}
        </div>

        <script>
            function generateLinks() {{
                const text = document.getElementById('guestListInput').value.trim();
                const container = document.getElementById('resultContainer');
                if(!text) {{
                    alert('Silakan masukkan minimal satu nama tamu!');
                    return;
                }}
                
                const names = text.split('\\n');
                let html = '';
                const baseUrl = window.location.origin + '/demo?id={tmpl_id}&to=';

                names.forEach((name, index) => {{
                    const cleanName = name.trim();
                    if(cleanName) {{
                        const specificUrl = baseUrl + encodeURIComponent(cleanName);
                        const waText = encodeURIComponent(`Halo *${cleanName}*, tanpa mengurangi rasa hormat, kami mengundang Bapak/Ibu/Saudara/i untuk menghadiri acara pernikahan {buyer_name}. Berikut link undangan digital kami:\\n\\n${specificUrl}`);
                        const waUrl = `https://wa.me/?text=${waText}`;

                        html += `
                        <div class="guest-item">
                            <div>
                                <strong style="color:#fff; display:block; margin-bottom:2px;">${cleanName}</strong>
                                <span style="font-size:9px; color:#71717a; word-break:break-all;">${specificUrl}</span>
                            </div>
                            <div class="link-btns">
                                <button onclick="navigator.clipboard.writeText('${specificUrl}'); alert('Link untuk ${cleanName} disalin!');">Salin</button>
                                <a href="${waUrl}" target="_blank" class="wa"><i class="fa-brands fa-whatsapp"></i> WA</a>
                            </div>
                        </div>
                        `;
                    }}
                }});
                container.innerHTML = html;
            }}
        </script>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type='text/html')

async def handle_admin(request):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # ---- Laporan pesanan client (flow [6]-[13]) ----
    pending_orders = cursor.execute(
        "SELECT id, code, slug, couple_name, whatsapp, amount, status, template_id, proof_filename, event_date "
        "FROM orders WHERE status IN ('pending_payment','awaiting_verification','rejected_payment') ORDER BY id DESC"
    ).fetchall()
    all_orders = cursor.execute(
        "SELECT id, code, slug, couple_name, whatsapp, amount, status, expires_at FROM orders ORDER BY id DESC LIMIT 100"
    ).fetchall()
    new_reports = sum(1 for r in pending_orders if r[6] == "awaiting_verification")
    csrf_tok = make_csrf_token((get_admin(request) or {}).get("id", 0))
    notif_script = """
    <script>
      var ADMIN_CSRF = "%s";
      function notifyBrowser(title, body) {
        if (!("Notification" in window)) return;
        if (Notification.permission === "granted") { new Notification(title, { body: body }); }
        else if (Notification.permission !== "denied") {
          Notification.requestPermission().then(function (p) {
            if (p === "granted") new Notification(title, { body: body });
          });
        }
      }
      async function checkNewReports() {
        try {
          var res = await fetch("/admin/orders/pending?json=1");
          if (!res.ok) return;
          var data = await res.json();
          var last = parseInt(localStorage.getItem("sm_last_order_id") || "0");
          var fresh = data.orders.filter(function (o) { return o.id > last && o.status === "awaiting_verification"; });
          if (fresh.length) {
            notifyBrowser("Laporan Pesanan Baru - SUKA MOTO",
              fresh.length + " bukti pembayaran menunggu verifikasi. Terbaru: " + fresh[0].couple_name + " (" + fresh[0].code + ")");
            var badge = document.getElementById("report-badge");
            if (badge) { badge.textContent = data.count; badge.style.display = "inline-block"; }
          }
          var maxId = data.orders.reduce(function (m, o) { return Math.max(m, o.id); }, last);
          localStorage.setItem("sm_last_order_id", String(maxId));
        } catch (e) {}
      }
      checkNewReports();
      setInterval(checkNewReports, 20000);
    </script>""" % _escape(csrf_tok)

    report_rows = ""
    for (oid, code, slug, cname, wa, amount, status, tid, proof, evdate) in pending_orders:
        pill = '<span class="status-pill st-%s">%s</span>' % (status, _escape(ORDER_STATUS_LABEL.get(status, status)))
        proof_html = ("<a href='/static_uploads/%s' target='_blank' style='color:#fbbf24;font-size:10px;'>[Lihat Bukti]</a>"
                      % urlquote(os.path.basename(proof))) if proof else "<span style='color:#71717a;font-size:10px;'>belum ada bukti</span>"
        wa_report = "https://wa.me/" + re.sub(r"\D", "", wa or "") + "?text=" + urlquote(
            "Halo %s, pesanan undangan %s (kode %s) dengan nominal %s sudah kami terima dan sedang kami proses." % (cname, cname, code, amount))
        wa_link_ok = wa_admin_link((oid, code, slug, cname, wa, amount, status), approve=True)
        wa_link_no = wa_admin_link((oid, code, slug, cname, wa, amount, status), approve=False)
        actions = ""
        if status == "awaiting_verification":
            actions += """
            <form action="/admin/order_action" method="POST" style="display:inline;">
                <input type="hidden" name="csrf_token" value="%s">
                <input type="hidden" name="order_id" value="%d"><input type="hidden" name="action" value="verify">
                <button type="submit" style="background:#34d399;color:#000;border:none;padding:4px 8px;border-radius:4px;font-size:10px;font-weight:bold;cursor:pointer;">&#10003; Verifikasi</button>
            </form>
            <form action="/admin/order_action" method="POST" style="display:inline;">
                <input type="hidden" name="csrf_token" value="%s">
                <input type="hidden" name="order_id" value="%d"><input type="hidden" name="action" value="reject">
                <button type="submit" style="background:#ef4444;color:#fff;border:none;padding:4px 8px;border-radius:4px;font-size:10px;font-weight:bold;cursor:pointer;">&#10060; Tolak</button>
            </form>""" % (_escape(csrf_tok), oid, _escape(csrf_tok), oid)
        if status == "pending_payment":
            actions += """
            <form action="/admin/order_action" method="POST" style="display:inline;">
                <input type="hidden" name="csrf_token" value="%s">
                <input type="hidden" name="order_id" value="%d"><input type="hidden" name="action" value="mark_paid">
                <button type="submit" style="background:#3b82f6;color:#fff;border:none;padding:4px 8px;border-radius:4px;font-size:10px;font-weight:bold;cursor:pointer;">Uang Masuk (tanpa bukti)</button>
            </form>""" % (_escape(csrf_tok), oid)
        if status in ("verified",):
            actions += """
            <form action="/admin/order_action" method="POST" style="display:inline;">
                <input type="hidden" name="csrf_token" value="%s">
                <input type="hidden" name="order_id" value="%d"><input type="hidden" name="action" value="process">
                <button type="submit" style="background:#a855f7;color:#fff;border:none;padding:4px 8px;border-radius:4px;font-size:10px;font-weight:bold;cursor:pointer;">Proses Undangan</button>
            </form>""" % (_escape(csrf_tok), oid)
        report_rows += """
        <tr style="border-bottom:1px solid #27272a;">
            <td style="padding:8px;"><b style="color:#fff;font-size:11px;">%s</b><br>
                <span style="font-size:9px;color:#fbbf24;">%s</span><br>%s<br>
                <span style="font-size:9px;color:#71717a;">WA: %s &bull; Acara: %s</span></td>
            <td style="padding:8px;font-size:10px;color:#34d399;">%s</td>
            <td style="padding:8px;">%s<br>%s</td>
            <td style="padding:8px;white-space:nowrap;">%s
                <div style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap;">
                  <a href="%s" target="_blank" style="background:#22c55e;color:#fff;padding:3px 6px;border-radius:4px;font-size:9px;text-decoration:none;font-weight:bold;">WA Konfirmasi</a>
                  <a href="%s" target="_blank" style="background:#27272a;color:#fff;padding:3px 6px;border-radius:4px;font-size:9px;text-decoration:none;">WA Link Aktif</a>
                  <a href="%s" target="_blank" style="background:#27272a;color:#fbbf24;padding:3px 6px;border-radius:4px;font-size:9px;text-decoration:none;">WA Tolak</a>
                </div>
            </td>
        </tr>""" % (_escape(cname), _escape(code), pill, _escape(wa), _escape(format_date_id(evdate)),
                    _escape(amount), proof_html, _escape(invite_url_for(slug)), actions,
                    wa_report if wa else "#", wa_link_ok, wa_link_no)
    if not report_rows:
        report_rows = "<tr><td colspan='4' style='text-align:center;color:#71717a;padding:10px;font-size:11px;'>Tidak ada pesanan yang menunggu tindakan.</td></tr>"

    archive_rows = ""
    for (oid, code, slug, cname, wa, amount, status, exp) in all_orders:
        pill = '<span class="status-pill st-%s">%s</span>' % (status, _escape(ORDER_STATUS_LABEL.get(status, status)))
        act_btn = ""
        if status == "processing":
            act_btn = """<form action="/admin/order_action" method="POST" style="display:inline;">
                <input type="hidden" name="csrf_token" value="%s">
                <input type="hidden" name="order_id" value="%d"><input type="hidden" name="action" value="activate">
                <button type="submit" style="background:#34d399;color:#000;border:none;padding:4px 8px;border-radius:4px;font-size:9px;font-weight:bold;cursor:pointer;">&#9654; AKTIFKAN</button></form>""" % (_escape(csrf_tok), oid)
        elif status == "active":
            act_btn = """<form action="/admin/order_action" method="POST" style="display:inline;">
                <input type="hidden" name="csrf_token" value="%s">
                <input type="hidden" name="order_id" value="%d"><input type="hidden" name="action" value="expire">
                <button type="submit" style="background:#71717a;color:#fff;border:none;padding:4px 8px;border-radius:4px;font-size:9px;cursor:pointer;">Nonaktifkan</button></form>""" % (_escape(csrf_tok), oid)
        archive_rows += """
        <tr style="border-bottom:1px solid #27272a;">
            <td style="padding:6px;font-size:10px;"><b>%s</b><br><span style="color:#71717a;">%s</span></td>
            <td style="padding:6px;font-size:10px;">%s</td>
            <td style="padding:6px;font-size:10px;color:#34d399;">%s</td>
            <td style="padding:6px;font-size:10px;">%s</td>
            <td style="padding:6px;">%s</td>
        </tr>""" % (_escape(cname), _escape(code), pill, _escape(amount),
                    _escape(exp or "-"), act_btn)
    if not archive_rows:
        archive_rows = "<tr><td colspan='5' style='text-align:center;color:#71717a;padding:10px;font-size:11px;'>Belum ada pesanan.</td></tr>"

    cursor.execute('SELECT id, name, subtitle FROM packages')
    pkgs = cursor.fetchall()

    cursor.execute('''
        SELECT t.id, t.name, t.price, p.name, t.discount, t.duration, t.is_top10 
        FROM templates t 
        JOIN packages p ON t.package_id = p.id
    ''')
    tmpls = cursor.fetchall()

    cursor.execute('SELECT id, filename, filepath FROM media_uploads ORDER BY id DESC')
    media_files = cursor.fetchall()
    conn.close()

    # Baca file homepage.html langsung untuk ditampilkan di textarea admin
    if os.path.exists(HOMEPAGE_FILE):
        with open(HOMEPAGE_FILE, "r", encoding="utf-8") as f:
            current_homepage_html = f.read()
    else:
        current_homepage_html = ""
    # S0 SECURITY: escape saat masuk <textarea> (file disimpan apa adanya).
    current_homepage_esc = _escape(current_homepage_html)

    # nilai pengaturan utk form admin
    _adm_account = get_setting(cursor, "payment_account", "")
    _adm_qris = get_setting(cursor, "payment_qris", "")
    _adm_wa = get_setting(cursor, "admin_whatsapp", "")

    pkg_options = ""
    for pid, pname, _ in pkgs:
        pkg_options += f'<option value="{pid}">{pname}</option>'

    pkg_rows = ""
    for pid, name, sub in pkgs:
        pkg_rows += f"""
        <tr style="border-bottom:1px solid #27272a;">
            <td style="padding:10px; font-weight:bold;">{_escape(name)}</td>
            <td style="padding:10px; color:#a1a1aa;">{_escape(sub)}</td>
            <td style="padding:10px;">
                <form action="/admin/delete_pkg" method="POST" style="display:inline;">
                    {csrf_field(request)}
                    <input type="hidden" name="id" value="{pid}">
                    <button type="submit" style="background:#ef4444; color:#fff; border:none; padding:4px 8px; border-radius:4px; cursor:pointer; font-size:10px;">Hapus</button>
                </form>
            </td>
        </tr>
        """

    tmpl_rows = ""
    for tid, tname, tprice, pname, tdisc, tdur, top10 in tmpls:
        top_badge = "<span style='background:#34d399; color:#000; padding:1px 4px; border-radius:3px; font-size:8px; font-weight:bold;'>Top 10</span>" if top10 else ""
        extra_info = f"<br><span style='font-size:9px; color:#fbbf24;'>{_escape(tdisc)} | {_escape(tdur)} {top_badge}</span>" if (tdisc or tdur or top10) else ""
        tmpl_rows += f"""
        <tr style="border-bottom:1px solid #27272a;">
            <td style="padding:10px; font-weight:bold;">{_escape(tname)} {extra_info}<br><span style="font-size:9px; color:#71717a;">Paket: {_escape(pname)}</span></td>
            <td style="padding:10px; color:#34d399; font-weight:bold;">{_escape(tprice)}</td>
            <td style="padding:10px;">
                <form action="/admin/delete_tmpl" method="POST" style="display:inline;">
                    {csrf_field(request)}
                    <input type="hidden" name="id" value="{tid}">
                    <button type="submit" style="background:#ef4444; color:#fff; border:none; padding:4px 8px; border-radius:4px; cursor:pointer; font-size:10px;">Hapus</button>
                </form>
            </td>
        </tr>
        """

    media_rows = ""
    for mid, mname, mpath in media_files:
        full_url = "/static_uploads/" + urlquote(mname)
        media_rows += f"""
        <tr style="border-bottom:1px solid #27272a;">
            <td style="padding:8px; word-break:break-all;">
                <a href="{_escape(full_url)}" target="_blank" style="color:#34d399; text-decoration:none; display:block; margin-bottom:4px;">{_escape(mname)}</a>
                <input type="text" readonly value="" class="auto-full-url" data-url="{_escape(full_url)}" onclick="this.select();" style="font-size:10px; padding:4px 6px; background:#121215; border:1px solid #3f3f46; color:#fbbf24; border-radius:4px; width:100%;">
            </td>
            <td style="padding:8px; text-align:right; white-space:nowrap; vertical-align:top;">
                <button type="button" onclick="navigator.clipboard.writeText(this.closest('tr').querySelector('.auto-full-url').value).then(() => {{ alert('Link aset berhasil disalin!'); }});" style="background:#27272a; color:#fbbf24; border:none; padding:4px 8px; border-radius:4px; font-size:9px; cursor:pointer; font-weight:bold;">Copy</button>
                <form action="/admin/delete_media" method="POST" style="display:inline;">
                    {csrf_field(request)}
                    <input type="hidden" name="id" value="{mid}">
                    <button type="submit" style="background:#ef4444; color:#fff; border:none; padding:4px 8px; border-radius:4px; font-size:9px; cursor:pointer; margin-left:4px;">Hapus</button>
                </form>
            </td>
        </tr>
        """

    if not media_rows:
        media_rows = "<tr><td colspan='2' style='text-align:center; color:#71717a; padding:10px; font-size:11px;'>Belum ada file di-upload.</td></tr>"

    admin_html = f"""
    <!DOCTYPE html>
    <html lang="id">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Admin Dashboard - SUKA MOTO</title>
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: sans-serif; }}
            body {{ background: #09090b; color: #f4f4f5; padding: 16px; }}
            .wrap {{ max-width: 600px; margin: 0 auto; background: #121215; padding: 20px; border-radius: 12px; border: 1px solid #27272a; }}
            input, select, textarea, button {{ padding: 10px 12px; margin: 6px 0; border-radius: 8px; border: 1px solid #3f3f46; background: #18181b; color: #fff; width: 100%; font-size: 13px; }}
            textarea {{ resize: vertical; height: 100px; font-family: monospace; font-size: 11px; }}
            .btn-save {{ background: #fbbf24; color: #000; font-weight: bold; cursor: pointer; border: none; margin-top: 10px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px; }}
            th {{ text-align: left; padding: 10px; border-bottom: 2px solid #3f3f46; color: #fbbf24; }}
            .section-box {{ background: #18181b; border: 1px solid #27272a; padding: 15px; border-radius: 10px; margin-bottom: 20px; }}
            {ORDER_CSS}
        </style>
    </head>
    <body>
        <div class="wrap">
            <h2 style="font-size: 16px; margin-bottom: 5px;">Panel Kontrol Admin
              <span id="report-badge" style="display:none;background:#ef4444;color:#fff;font-size:10px;border-radius:99px;padding:2px 8px;vertical-align:middle;">0</span>
            </h2>
            <p style="font-size:11px; color:#a1a1aa; margin-bottom:20px;">
                <a href="/" style="color:#fbbf24; text-decoration:none;">&larr; Kembali ke Beranda</a>
                &nbsp;|&nbsp; Login sebagai: <b style="color:#34d399;">{_escape((get_admin(request) or {}).get('username', ''))}</b>
                &nbsp;|&nbsp; <a href="/admin/logout" style="color:#ef4444; text-decoration:none;">Logout</a>
            </p>

            <div class="section-box" style="border-left:3px solid #fbbf24;">
                <h3 style="font-size:13px; margin-bottom:8px; color:#fbbf24;">Laporan Pesanan Client (Perlu Tindakan)
                    <span style="background:#27272a;color:#fff;font-size:10px;border-radius:99px;padding:2px 8px;">{len(pending_orders)}</span></h3>
                <p style="font-size:10px;color:#71717a;margin-bottom:8px;">Notifikasi browser aktif (polling tiap 20 detik). Alur: Verifikasi/Tolak pembayaran &rarr; Proses &rarr; AKTIFKAN &rarr; kirim link ke client via tombol WhatsApp.</p>
                <table>
                    <tr><th>Pesanan / Pasangan</th><th>Nominal</th><th>Bukti &amp; Link Subdomain</th><th>Aksi Admin</th></tr>
                    {report_rows}
                </table>
            </div>

            <div class="section-box">
                <h3 style="font-size:13px; margin-bottom:8px; color:#fbbf24;">Semua Pesanan (Timeline [7]-[13])</h3>
                <table>
                    <tr><th>Pasangan / Kode</th><th>Status</th><th>Nominal</th><th>Expires</th><th>Aksi</th></tr>
                    {archive_rows}
                </table>
            </div>

            <div class="section-box">
                <h3 style="font-size:13px; margin-bottom:8px; color:#fbbf24;">Pengaturan Pembayaran &amp; WhatsApp Admin</h3>
                <form action="/admin/save_settings" method="POST">
                    {csrf_field(request)}
                    <label style="font-size:11px;color:#a1a1aa;display:block;">Rekening Bank</label>
                    <input type="text" name="payment_account" value="{_escape(_adm_account)}">
                    <label style="font-size:11px;color:#a1a1aa;display:block;margin-top:6px;">QRIS / E-Wallet</label>
                    <input type="text" name="payment_qris" value="{_escape(_adm_qris)}">
                    <label style="font-size:11px;color:#a1a1aa;display:block;margin-top:6px;">Nomor WhatsApp Admin (untuk notifikasi/link laporan)</label>
                    <input type="text" name="admin_whatsapp" value="{_escape(_adm_wa)}">
                    <button type="submit" class="btn-save">Simpan Pengaturan</button>
                </form>
            </div>

            <div class="section-box">
                <h3 style="font-size:13px; margin-bottom:8px; color:#fbbf24;">Edit File Fisik Halaman Beranda (homepage.html)</h3>
                <form action="/admin/update_homepage" method="POST">
                    {csrf_field(request)}
                    <label style="font-size: 11px; color: #a1a1aa; display:block; margin-top:4px;">Isi file homepage.html:</label>
                    <textarea name="homepage_html" required style="height:120px;">{current_homepage_esc}</textarea>
                    <button type="submit" class="btn-save">Simpan ke File homepage.html</button>
                </form>
            </div>

            <div class="section-box">
                <h3 style="font-size:13px; margin-bottom:8px; color:#fbbf24;">File Manager (Asset Hosting)</h3>
                <form action="/admin/upload_media" method="POST" enctype="multipart/form-data">
                    {csrf_field(request)}
                    <label style="font-size: 11px; color: #a1a1aa; display:block;">Upload File / Gambar Aset:</label>
                    <input type="file" name="file" required accept=".jpg,.jpeg,.png,.gif,.webp,.pdf" style="background:#121215; padding:6px;">
                    <small style="font-size:10px; color:#71717a;">Format diizinkan: jpg, jpeg, png, gif, webp, pdf — maks 5 MB.</small>
                    <button type="submit" class="btn-save">Upload & Dapatkan Link</button>
                </form>
                <h4 style="font-size:11px; margin-top:15px; color:#a1a1aa; margin-bottom:5px;">Daya Simpan Aset (Copy Link):</h4>
                <table>
                    <tr><th>Nama File / Link Aset</th><th style="text-align:right;">Aksi</th></tr>
                    {media_rows}
                </table>
            </div>

            <div class="section-box">
                <h3 style="font-size:13px; margin-bottom:8px; color:#fbbf24;">Tambah Paket Utama</h3>
                <form action="/admin/add_pkg" method="POST">
                    {csrf_field(request)}
                    <input type="text" name="name" placeholder="Nama Paket (Contoh: Platinum VIP)" required>
                    <input type="text" name="subtitle" placeholder="Subjudul (Contoh: All-in-One Exclusive)" required>
                    <input type="text" name="image_url" placeholder="URL Gambar Cover Card" value="https://images.unsplash.com/photo-1519741497674-611481863552?auto=format&fit=crop&w=600&q=80" required>
                    <button type="submit" class="btn-save">Simpan Paket</button>
                </form>
                <h4 style="font-size:11px; margin-top:15px; color:#a1a1aa; margin-bottom:5px;">Daftar Paket:</h4>
                <table>
                    <tr><th>Paket</th><th>Keterangan</th><th>Aksi</th></tr>
                    {pkg_rows}
                </table>
            </div>

            <div class="section-box">
                <h3 style="font-size:13px; margin-bottom:8px; color:#fbbf24;">Tambah Template & Kodingan HTML</h3>
                <form action="/admin/add_tmpl" method="POST">
                    {csrf_field(request)}
                    <select name="package_id" required>
                        <option value="">-- Pilih Kategori Paket --</option>
                        {pkg_options}
                    </select>
                    <input type="text" name="name" placeholder="Nama Template (Contoh: Royal Velvet)" required>
                    <input type="text" name="price" placeholder="Harga (Contoh: Rp 250.000)" required>
                    <input type="text" name="discount" placeholder="Keterangan Diskon (Cth: Diskon 20%)">
                    <input type="text" name="duration" placeholder="Durasi Aktif (Cth: Aktif 1 Tahun)">
                    <div style="display: flex; align-items: center; gap: 8px; margin: 6px 0; font-size: 12px; color: #fff;">
                        <input type="checkbox" name="is_top10" value="1" style="width: auto; margin:0;"> Masukkan ke 10 Template Terbaik (Homepage)
                    </div>
                    <input type="text" name="image_url" placeholder="URL Gambar Preview Template" value="https://images.unsplash.com/photo-1520854221256-17451cc331bf?auto=format&fit=crop&w=400&q=80" required>
                    <label style="font-size: 11px; color: #a1a1aa; display:block; margin-top:8px;">Source Code HTML Template:</label>
                    <textarea name="html_code" placeholder="<!DOCTYPE html>... Paste kodingan HTML undangan web di sini ..." required></textarea>
                    <button type="submit" class="btn-save">Simpan Template & Kodingan</button>
                </form>
                <h4 style="font-size:11px; margin-top:15px; color:#a1a1aa; margin-bottom:5px;">Daftar Template:</h4>
                <table>
                    <tr><th>Template / Paket</th><th>Harga</th><th>Aksi</th></tr>
                    {tmpl_rows}
                </table>
            </div>
        </div>
        <script>
            document.querySelectorAll('.auto-full-url').forEach(input => {{
                input.value = window.location.origin + input.getAttribute('data-url');
            }});
            {notif_script}
        </script>
    </body>
    </html>
    """
    return web.Response(text=admin_html, content_type='text/html')

async def handle_admin_orders_pending(request):
    """Endpoint JSON untuk polling notifikasi browser admin."""
    refresh_expired_orders()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, code, couple_name, whatsapp, amount, status FROM orders "
                "WHERE status IN ('pending_payment','awaiting_verification','rejected_payment') ORDER BY id DESC LIMIT 50")
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return web.json_response({"count": len(rows), "orders": rows})

async def handle_admin_order_action(request):
    """Konfirmasi admin: verify/reject/mark_paid/process/activate/expire + catat timeline."""
    data = await request.post()
    adm = get_admin(request)
    if not check_csrf_token(adm["id"], data.get("csrf_token", "")):
        raise web.HTTPForbidden(text="CSRF token tidak valid. Silakan login ulang.")
    oid = data.get("order_id")
    action = data.get("action", "")
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders WHERE id = ?", (oid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise web.HTTPFound("/admin")
    o = dict(zip([c[0] for c in cur.description], row))
    status = o["status"]
    if action == "verify" and status == "awaiting_verification":
        set_order_status(cur, o["id"], "verified", "Bukti pembayaran diverifikasi admin " + str(adm.get("username")))
    elif action == "reject" and status == "awaiting_verification":
        reason = (data.get("reason") or "").strip()[:200] or "Data transfer tidak cocok dengan nominal/kode pesanan."
        cur.execute("UPDATE orders SET status='rejected_payment', reject_reason=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (reason, o["id"]))
        add_order_event(cur, o["id"], "rejected_payment", "Ditolak admin: " + reason)
    elif action == "mark_paid" and status == "pending_payment":
        set_order_status(cur, o["id"], "verified", "Uang masuk dikonfirmasi admin (tanpa upload bukti)")
    elif action == "process" and status == "verified":
        set_order_status(cur, o["id"], "processing", "Undangan masuk antrean produksi")
    elif action == "activate" and status in ("processing", "verified"):
        cur.execute('''SELECT t.duration, p.duration, o.event_date, o.amount FROM orders o
                       LEFT JOIN templates t ON t.id = o.template_id
                       LEFT JOIN packages p ON p.id = o.package_id WHERE o.id = ?''', (o["id"],))
        exp = compute_expires_at(cur.fetchone())
        cur.execute("UPDATE orders SET status='active', expires_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (exp, o["id"]))
        add_order_event(cur, o["id"], "active", "Undangan diaktifkan -> " + invite_url_for(o["slug"]) + " (exp " + exp + ")")
    elif action == "expire" and status == "active":
        set_order_status(cur, o["id"], "expired", "Dinonaktifkan manual oleh admin")
    conn.commit()
    conn.close()
    raise web.HTTPFound("/admin")

async def handle_admin_save_settings(request):
    data = await request.post()
    adm = get_admin(request)
    if not check_csrf_token(adm["id"], data.get("csrf_token", "")):
        raise web.HTTPForbidden(text="CSRF token tidak valid. Silakan login ulang.")
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    for key in ("payment_account", "payment_qris", "admin_whatsapp"):
        val = (data.get(key) or "").strip()[:200]
        if val:
            cur.execute('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                        (key, val))
    conn.commit()
    conn.close()
    raise web.HTTPFound("/admin")

async def handle_update_homepage(request):
    data = await request.post()
    new_html = data.get('homepage_html', '')
    # Langsung simpan menimpa file fisik 'homepage.html'
    with open(HOMEPAGE_FILE, "w", encoding="utf-8") as f:
        f.write(new_html)
    raise web.HTTPFound('/admin')

async def handle_upload_media(request):
    # S0 SECURITY: perbaikan path traversal + validasi tipe/ukuran file.
    admin = get_admin(request)  # middleware melewatkan multipart tanpa cek CSRF,
                                # jadi validasi CSRF dilakukan di sini.
    reader = await request.multipart()
    csrf_ok = False
    field = await reader.next()
    error = ""
    saved_name = None
    while field is not None:
        if field.name == "csrf_token":
            val = (await field.read(decode=True)).decode("utf-8", "replace")
            csrf_ok = check_csrf_token(admin["id"], val)
            field = await reader.next()
        elif field.name == "file":
            break  # field file ketemu; hentikan iterasi
        else:
            field = await reader.next()
    if not csrf_ok:
        raise web.HTTPForbidden(text="CSRF token tidak valid. Silakan login ulang.")
    if field and field.filename:
        original = os.path.basename(field.filename)  # buang semua komponen path ../
        ext = os.path.splitext(original)[1].lower()
        if ext not in ALLOWED_UPLOAD_EXTS:
            error = "badtype"
        else:
            declared = (field.headers.get("Content-Type") if field.headers else "") or ""
            guessed = mimetypes.guess_type(original)[0] or ""
            content_type = declared.split(";")[0].strip().lower()
            if content_type and content_type not in ALLOWED_UPLOAD_MIMES:
                error = "badmime"
            elif guessed and guessed not in ALLOWED_UPLOAD_MIMES:
                error = "badmime"
            else:
                safe_stem = re.sub(r"[^A-Za-z0-9_-]", "_", os.path.splitext(original)[0])[:60] or "file"
                saved_name = "%s_%d%s" % (safe_stem, int(time.time() * 1000), ext)
                filepath = os.path.join(UPLOAD_DIR, saved_name)
                size = 0
                oversize = False
                with open(filepath, 'wb') as f:
                    while True:
                        chunk = await field.read_chunk()
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_UPLOAD_BYTES:
                            oversize = True
                            break
                        f.write(chunk)
                if oversize or size == 0:
                    try:
                        os.remove(filepath)
                    except OSError:
                        pass
                    saved_name = None
                    error = "oversize" if oversize else "empty"
                else:
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    # filename = nama tersimpan yang aman; filepath mengikuti pola lama
                    cursor.execute('INSERT INTO media_uploads (filename, filepath) VALUES (?, ?)',
                                   (saved_name, os.path.join(UPLOAD_DIR, saved_name)))
                    conn.commit()
                    conn.close()
    if error:
        raise web.HTTPFound('/admin?upload_error=' + error)
    raise web.HTTPFound('/admin')

async def handle_delete_media(request):
    data = await request.post()
    mid = data.get('id')
    if mid:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT filename FROM media_uploads WHERE id = ?', (mid,))
        res = cursor.fetchone()
        if res:
            # S0 SECURITY: cegah path traversal saat penghapusan file.
            safe_name = os.path.basename(res[0])
            fpath = os.path.realpath(os.path.join(UPLOAD_DIR, safe_name))
            upload_root = os.path.realpath(UPLOAD_DIR)
            if fpath.startswith(upload_root + os.sep) and os.path.isfile(fpath):
                os.remove(fpath)
            cursor.execute('DELETE FROM media_uploads WHERE id = ?', (mid,))
            conn.commit()
        conn.close()
    raise web.HTTPFound('/admin')

async def handle_add_pkg(request):
    data = await request.post()
    if data.get('name') and data.get('subtitle'):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO packages (name, subtitle, image_url) VALUES (?, ?, ?)', 
                       (data.get('name'), data.get('subtitle'), data.get('image_url')))
        conn.commit()
        conn.close()
    raise web.HTTPFound('/admin')

async def handle_delete_pkg(request):
    data = await request.post()
    if data.get('id'):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM packages WHERE id = ?', (data.get('id'),))
        cursor.execute('DELETE FROM templates WHERE package_id = ?', (data.get('id'),))
        conn.commit()
        conn.close()
    raise web.HTTPFound('/admin')

async def handle_add_tmpl(request):
    data = await request.post()
    if data.get('package_id') and data.get('name') and data.get('price'):
        is_top10 = 1 if data.get('is_top10') == '1' else 0
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO templates (package_id, name, price, discount, duration, image_url, html_code, is_top10) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('package_id'), 
            data.get('name'), 
            data.get('price'), 
            data.get('discount'), 
            data.get('duration'), 
            data.get('image_url'), 
            data.get('html_code'),
            is_top10
        ))
        conn.commit()
        conn.close()
    raise web.HTTPFound('/admin')

async def handle_delete_tmpl(request):
    data = await request.post()
    if data.get('id'):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM templates WHERE id = ?', (data.get('id'),))
        conn.commit()
        conn.close()
    raise web.HTTPFound('/admin')

app = web.Application(middlewares=[security_middleware])
app.router.app_get = app.router.add_get # fallback safety
app.router.add_get('/', handle_index)
app.router.add_get('/templates', handle_templates)
app.router.add_get('/template-action', handle_template_action)
app.router.add_get('/demo', handle_demo)
app.router.add_get('/editor', handle_editor)
app.router.add_get('/checkout', handle_checkout)
app.router.add_get('/guestbook', handle_guestbook)

# S0 SECURITY: autentikasi admin server-side (menggantikan PIN client-side)
app.router.add_get('/admin/login', handle_admin_login)
app.router.add_post('/admin/login', handle_admin_login_post)
app.router.add_get('/admin/logout', handle_admin_logout)
app.router.add_get('/admin', handle_admin)

app.router.add_static('/static_uploads/', path=UPLOAD_DIR, name='static_uploads')

# Semua endpoint POST /admin/* kini diproteksi security_middleware:
# wajib session cookie valid + CSRF token.
app.router.add_post('/admin/update_homepage', handle_update_homepage)
app.router.add_post('/admin/upload_media', handle_upload_media)
app.router.add_post('/admin/delete_media', handle_delete_media)
app.router.add_post('/admin/add_pkg', handle_add_pkg)
app.router.add_post('/admin/delete_pkg', handle_delete_pkg)
app.router.add_post('/admin/add_tmpl', handle_add_tmpl)
app.router.add_post('/admin/delete_tmpl', handle_delete_tmpl)

# === ROUTING ALUR CLIENT (flow [1]-[13]) ===
app.router.add_get('/start', handle_start)                       # [2] Pilih Paket
app.router.add_get('/form', handle_form)                         # [4] Isi Data Undangan
app.router.add_post('/preview', handle_preview)                  # [5] Preview Undangan
app.router.add_post('/submit-order', handle_submit_order)        # [6] Kirim Pesanan -> Order dibuat
app.router.add_get('/payment', handle_payment)                   # [7] Pembayaran: nominal, rekening/QRIS, kode unik
app.router.add_get('/upload-proof', handle_upload_proof)         # [8] Upload Bukti Pembayaran
app.router.add_post('/upload-proof', handle_upload_proof_post)   # [8] terkirim -> [9] Menunggu Verifikasi
app.router.add_get('/track', handle_track)                       # status pesanan [7]-[13] via kode unik
app.router.add_get('/my-orders', handle_my_orders)               # daftar pesanan via nomor WhatsApp
app.router.add_get('/u/{slug}', handle_invite_subdomain)         # fallback /undangan tanpa wildcard subdomain

# === ADMIN ORDER PIPELINE (laporan + konfirmasi + kirim link WA) ===
app.router.add_get('/admin/orders/pending', handle_admin_orders_pending)   # polling notifikasi browser
app.router.add_post('/admin/order_action', handle_admin_order_action)      # verify/reject/process/activate/expire
app.router.add_post('/admin/save_settings', handle_admin_save_settings)    # rekening/QRIS/WA admin

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=9000)

