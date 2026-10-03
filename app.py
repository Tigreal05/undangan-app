import os
import sqlite3
import aiohttp
from aiohttp import web

DB_NAME = "undangan.db"
UPLOAD_DIR = "./static_uploads"
HOMEPAGE_FILE = "homepage.html"

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

    conn.close()

init_db()

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
        function accessAdmin() {
            const pin = prompt("Masukkan PIN Keamanan Admin:");
            if (pin === "110202") {
                window.location.href = "/admin";
            } else if (pin !== null) {
                alert("PIN Salah! Akses ditolak.");
            }
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
                    <a href="/editor?id={tmpl_id}" class="btn-build"><i class="fa-solid fa-wand-magic-sparkles"></i> Buat Undangan Ini</a>
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

    pkg_options = ""
    for pid, pname, _ in pkgs:
        pkg_options += f'<option value="{pid}">{pname}</option>'

    pkg_rows = ""
    for pid, name, sub in pkgs:
        pkg_rows += f"""
        <tr style="border-bottom:1px solid #27272a;">
            <td style="padding:10px; font-weight:bold;">{name}</td>
            <td style="padding:10px; color:#a1a1aa;">{sub}</td>
            <td style="padding:10px;">
                <form action="/admin/delete_pkg" method="POST" style="display:inline;">
                    <input type="hidden" name="id" value="{pid}">
                    <button type="submit" style="background:#ef4444; color:#fff; border:none; padding:4px 8px; border-radius:4px; cursor:pointer; font-size:10px;">Hapus</button>
                </form>
            </td>
        </tr>
        """

    tmpl_rows = ""
    for tid, tname, tprice, pname, tdisc, tdur, top10 in tmpls:
        top_badge = "<span style='background:#34d399; color:#000; padding:1px 4px; border-radius:3px; font-size:8px; font-weight:bold;'>Top 10</span>" if top10 else ""
        extra_info = f"<br><span style='font-size:9px; color:#fbbf24;'>{tdisc} | {tdur} {top_badge}</span>" if (tdisc or tdur or top10) else ""
        tmpl_rows += f"""
        <tr style="border-bottom:1px solid #27272a;">
            <td style="padding:10px; font-weight:bold;">{tname} {extra_info}<br><span style="font-size:9px; color:#71717a;">Paket: {pname}</span></td>
            <td style="padding:10px; color:#34d399; font-weight:bold;">{tprice}</td>
            <td style="padding:10px;">
                <form action="/admin/delete_tmpl" method="POST" style="display:inline;">
                    <input type="hidden" name="id" value="{tid}">
                    <button type="submit" style="background:#ef4444; color:#fff; border:none; padding:4px 8px; border-radius:4px; cursor:pointer; font-size:10px;">Hapus</button>
                </form>
            </td>
        </tr>
        """

    media_rows = ""
    for mid, mname, mpath in media_files:
        full_url = f"/static_uploads/{mname}"
        media_rows += f"""
        <tr style="border-bottom:1px solid #27272a;">
            <td style="padding:8px; word-break:break-all;">
                <a href="{full_url}" target="_blank" style="color:#34d399; text-decoration:none; display:block; margin-bottom:4px;">{mname}</a>
                <input type="text" readonly value="" class="auto-full-url" data-url="{full_url}" onclick="this.select();" style="font-size:10px; padding:4px 6px; background:#121215; border:1px solid #3f3f46; color:#fbbf24; border-radius:4px; width:100%;">
            </td>
            <td style="padding:8px; text-align:right; white-space:nowrap; vertical-align:top;">
                <button type="button" onclick="navigator.clipboard.writeText(this.closest('tr').querySelector('.auto-full-url').value).then(() => {{ alert('Link aset berhasil disalin!'); }});" style="background:#27272a; color:#fbbf24; border:none; padding:4px 8px; border-radius:4px; font-size:9px; cursor:pointer; font-weight:bold;">Copy</button>
                <form action="/admin/delete_media" method="POST" style="display:inline;">
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
        </style>
    </head>
    <body>
        <div class="wrap">
            <h2 style="font-size: 16px; margin-bottom: 5px;">Panel Kontrol Admin</h2>
            <p style="font-size:11px; color:#a1a1aa; margin-bottom:20px;"><a href="/" style="color:#fbbf24; text-decoration:none;">&larr; Kembali ke Beranda</a></p>
            
            <div class="section-box">
                <h3 style="font-size:13px; margin-bottom:8px; color:#fbbf24;">Edit File Fisik Halaman Beranda (homepage.html)</h3>
                <form action="/admin/update_homepage" method="POST">
                    <label style="font-size: 11px; color: #a1a1aa; display:block; margin-top:4px;">Isi file homepage.html:</label>
                    <textarea name="homepage_html" required style="height:120px;">{current_homepage_html}</textarea>
                    <button type="submit" class="btn-save">Simpan ke File homepage.html</button>
                </form>
            </div>

            <div class="section-box">
                <h3 style="font-size:13px; margin-bottom:8px; color:#fbbf24;">File Manager (Asset Hosting)</h3>
                <form action="/admin/upload_media" method="POST" enctype="multipart/form-data">
                    <label style="font-size: 11px; color: #a1a1aa; display:block;">Upload File / Gambar Aset:</label>
                    <input type="file" name="file" required style="background:#121215; padding:6px;">
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
        </script>
    </body>
    </html>
    """
    return web.Response(text=admin_html, content_type='text/html')

async def handle_update_homepage(request):
    data = await request.post()
    new_html = data.get('homepage_html', '')
    # Langsung simpan menimpa file fisik 'homepage.html'
    with open(HOMEPAGE_FILE, "w", encoding="utf-8") as f:
        f.write(new_html)
    raise web.HTTPFound('/admin')

async def handle_upload_media(request):
    reader = await request.multipart()
    field = await reader.next()
    if field and field.filename:
        filename = field.filename
        filepath = os.path.join(UPLOAD_DIR, filename)
        with open(filepath, 'wb') as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                f.write(chunk)
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO media_uploads (filename, filepath) VALUES (?, ?)', (filename, filepath))
        conn.commit()
        conn.close()
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
            fpath = os.path.join(UPLOAD_DIR, res[0])
            if os.path.exists(fpath):
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

app = web.Application()
app.router.app_get = app.router.add_get # fallback safety
app.router.add_get('/', handle_index)
app.router.add_get('/templates', handle_templates)
app.router.add_get('/template-action', handle_template_action)
app.router.add_get('/demo', handle_demo)
app.router.add_get('/editor', handle_editor)
app.router.add_get('/checkout', handle_checkout)
app.router.add_get('/guestbook', handle_guestbook)
app.router.add_get('/admin', handle_admin)

app.router.add_static('/static_uploads/', path=UPLOAD_DIR, name='static_uploads')

app.router.add_post('/admin/update_homepage', handle_update_homepage)
app.router.add_post('/admin/upload_media', handle_upload_media)
app.router.add_post('/admin/delete_media', handle_delete_media)
app.router.add_post('/admin/add_pkg', handle_add_pkg)
app.router.add_post('/admin/delete_pkg', handle_delete_pkg)
app.router.add_post('/admin/add_tmpl', handle_add_tmpl)
app.router.add_post('/admin/delete_tmpl', handle_delete_tmpl)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=9000)

