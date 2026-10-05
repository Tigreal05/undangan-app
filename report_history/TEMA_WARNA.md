# Tema Warna Project Undangan

Dokumen ini mencatat palet warna yang saat ini digunakan pada antarmuka pemesanan, panel admin, dan template undangan. Palet template memang beragam karena tiap desain undangan memiliki identitas visual sendiri; antarmuka aplikasi memakai palet merek yang lebih konsisten.

## Antarmuka Aplikasi dan Panel Admin

Halaman pemesanan dan dashboard admin menggunakan tema gelap charcoal dengan aksen emas dan hijau.

| Peran             | Warna                 | Kode                 | Penggunaan                            |
| ----------------- | --------------------- | -------------------- | ------------------------------------- |
| Latar halaman     | Charcoal hampir hitam | `#09090b`            | Latar utama halaman                   |
| Latar kontainer   | Charcoal gelap        | `#0c0c0e`            | Area konten customer                  |
| Permukaan         | Charcoal              | `#121215`, `#18181b` | Kartu, form, section dashboard        |
| Garis dan pemisah | Abu-abu gelap         | `#27272a`, `#3f3f46` | Border, tabel, kontrol form           |
| Teks utama        | Putih hangat          | `#f4f4f5`, `#ffffff` | Judul dan isi utama                   |
| Teks sekunder     | Abu-abu terang        | `#a1a1aa`            | Label dan keterangan                  |
| Teks redup        | Abu-abu sedang        | `#71717a`            | Metadata dan petunjuk sekunder        |
| Aksen utama       | Emas terang           | `#fbbf24`            | Tombol utama, heading, tautan penting |
| Aksen positif     | Hijau mint            | `#34d399`            | Harga, status aktif, informasi sukses |
| Aksi WhatsApp     | Hijau                 | `#22c55e`            | Tombol berbagi melalui WhatsApp       |
| Bahaya            | Merah                 | `#ef4444`            | Logout, hapus, status ditolak         |

### Warna Status Pesanan

| Status              | Latar     | Teks      |
| ------------------- | --------- | --------- |
| Menunggu pembayaran | `#78350f` | `#fcd34d` |
| Menunggu verifikasi | `#1e3a8a` | `#93c5fd` |
| Pembayaran ditolak  | `#7f1d1d` | `#fecaca` |
| Terverifikasi       | `#14532d` | `#86efac` |
| Sedang diproses     | `#3b0764` | `#d8b4fe` |
| Aktif               | `#064e3b` | `#34d399` |
| Kedaluwarsa         | `#27272a` | `#a1a1aa` |

Warna status membantu pemindaian cepat. Label status tetap harus ditampilkan supaya status dapat dipahami tanpa mengandalkan warna saja.

## Tema Template Undangan

### Imperial Modern

File: `templates/3_Imperial-Modern.html`

Palet ivory, merah, emas, dan marun memberi kesan formal serta tradisional-modern.

| Peran         | Kode                   |
| ------------- | ---------------------- |
| Ivory         | `#faf9f6`              |
| Merah         | `#c8102e`              |
| Emas          | `#d4af37`              |
| Tinta gelap   | `#1a1a1a`              |
| Marun         | `#4a0e17`              |
| Latar gradasi | `#f7f3ec` ke `#e5dcca` |

### Black Champagne Gold Ivory

File: `templates/black-champagne-gold-ivory.html`

Palet hitam, ivory, dan champagne gold menghasilkan nuansa editorial dan mewah dengan kontras gelap-terang yang kuat.

| Peran           | Kode      |
| --------------- | --------- |
| Hitam           | `#080807` |
| Permukaan gelap | `#151412` |
| Ivory           | `#f4efe4` |
| Warm ivory      | `#e9e0d0` |
| Champagne gold  | `#c7a86b` |
| Gold terang     | `#dec58f` |

### Dusty Rose Wedding

File: `templates/dusty_rose_wedding.html`

Palet cream, dusty rose, dan burgundy terasa lembut serta romantis. Pembagian warna yang ditulis pada token CSS menunjukkan cream sebagai warna dominan, dengan rose dan burgundy sebagai aksen.

| Peran          | Kode      |
| -------------- | --------- |
| Cream          | `#fff8f1` |
| Cream lembut   | `#f4e8df` |
| Dusty rose     | `#d6a6a5` |
| Rose gelap     | `#b9787b` |
| Burgundy       | `#6d2637` |
| Burgundy gelap | `#421923` |

### Islamic Traditional

File: `templates/template8_islamic_traditional.html`

Palet hijau tua dan emas memberi identitas Islami klasik, dengan permukaan hijau pucat dan teks gelap untuk bagian konten.

| Peran         | Kode      |
| ------------- | --------- |
| Hijau utama   | `#1e5128` |
| Emas          | `#d4af37` |
| Hijau gelap   | `#112013` |
| Latar         | `#f5f7f2` |
| Teks          | `#2b2b2b` |
| Teks sekunder | `#6c757d` |

## Penilaian dan Catatan

- Aplikasi transaksi dan admin sudah memiliki karakter yang cukup jelas: charcoal sebagai dasar, emas sebagai aksen utama, dan hijau untuk informasi positif.
- Warna emas tidak seragam antarbagian. UI memakai `#fbbf24`, sementara beberapa template memakai `#d4af37` atau champagne `#c7a86b`. Ini sesuai untuk desain undangan yang berbeda, tetapi halaman aplikasi sebaiknya memakai satu token merek.
- `#71717a` digunakan untuk teks kecil di latar gelap. Untuk informasi penting atau teks berukuran kecil, gunakan warna sekunder yang lebih terang seperti `#a1a1aa`.
- Status proses saat ini menggunakan ungu `#3b0764` dan `#d8b4fe`, berbeda dari aksen emas-hijau aplikasi. Pertahankan bila memang dibutuhkan sebagai pembeda status; jangan jadikan aksen dekoratif utama.
- `admin_page.html` berisi tampilan admin statis lama, sedangkan dashboard aktif dirender dari `app.py`. Dua sumber tampilan dapat menyebabkan warna dan komponen admin menyimpang jika sama-sama dipelihara.
- Tiap template undangan sebaiknya mempertahankan paletnya sendiri. Konsistensi merek cukup dijaga pada katalog, checkout, status pesanan, dan panel admin.

## Panduan Pemakaian

1. Gunakan charcoal untuk latar halaman aplikasi, dengan permukaan yang sedikit lebih terang untuk kontainer dan kontrol.
2. Gunakan emas untuk tindakan utama dan navigasi penting. Pastikan teks di atas tombol emas berwarna gelap.
3. Gunakan hijau hanya untuk keberhasilan, status aktif, harga, dan tautan berbagi yang relevan.
4. Gunakan merah untuk tindakan destruktif dan kondisi gagal; jangan gunakan merah hanya sebagai dekorasi.
5. Pertahankan teks utama berwarna terang dan hindari teks metadata yang terlalu redup untuk informasi yang perlu dibaca.
6. Jangan menyampaikan status hanya melalui warna; sertakan label teks yang jelas.
7. Pertahankan identitas masing-masing template undangan dan hindari menerapkan palet dashboard secara paksa ke desainnya.