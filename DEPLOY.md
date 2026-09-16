# Deployment XR-App

Aplikasi ini memproses **rekening koran nasabah**: nama pemilik, nomor
rekening, saldo, dan seluruh lawan transaksinya. Itu menentukan hampir semua
keputusan di bawah — termasuk beberapa yang kelihatannya cuma soal teknis.

---

## 1. Sebelum memilih tempatnya: tanya kepatuhan dulu

Di Indonesia, data keuangan pribadi termasuk kategori yang perlakuannya lebih
ketat di bawah UU Pelindungan Data Pribadi. Kalau XR-App dipakai di lembaga
yang diawasi OJK, **penempatan data dan pemakaian penyedia pihak ketiga/cloud
punya aturannya sendiri**.

Dokumen ini sengaja tidak menyebut pasal atau angka spesifik — itu harus
dikonfirmasi tim kepatuhan Anda. Yang penting disadari: jawabannya
**menentukan** pilihan deployment, bukan mengikutinya. Tanyakan sebelum
memilih penyedia, bukan sesudah.

---

## 2. Topologi yang direkomendasikan

**On-premise / jaringan internal, satu VM, aplikasi dalam container.**

```
[browser cabang]
      │  hanya lewat jaringan kantor / VPN — TIDAK diekspos ke internet
      ▼
[nginx]  TLS · batas unggah 64 MB · proxy timeout 180 dtk
      │
      ▼
[gunicorn]  worker sync · timeout 180 dtk · daur ulang tiap 100 request
      │
      ▼
[XR-App]  uploads/ sementara (dihapus di `finally`) · Excel di memori
```

Alasannya bukan "yang paling canggih", tapi yang paling cocok dengan bentuk
aplikasi ini:

- Pemakainya internal (pusat & cabang), jadi tidak butuh internet publik.
  Tidak mengekspos berarti menghapus satu kelas risiko, bukan menambalnya.
- Datanya tidak keluar perimeter institusi, sehingga pertanyaan kepatuhan yang
  paling sulit tidak perlu dijawab.
- Bebannya CPU-bound, konkurensi rendah, satu penyewa. Tidak ada yang
  diuntungkan dari autoscaling atau arsitektur terdistribusi.

**Serverless sebaiknya dihindari** untuk saat ini. Bukan karena tidak bisa
(sebagian penyedia mengizinkan timeout panjang), tapi karena beban CPU puluhan
detik per request adalah model biaya terburuk bagi serverless, dan cold start
pada dependensi seberat pdfplumber terasa oleh pemakainya.

Kalau tetap harus di cloud: taruh di VPC privat penyedia yang sudah dipakai
institusi, akses lewat VPN/private link — bukan endpoint publik.

---

## 3. Ukuran mesin

Diukur pada PDF referensi terbesar (Mandiri Kopra, 5,0 MB, **257 halaman**):

| | |
|---|---|
| Ekstraksi | 19,6 detik |
| Penyusunan Excel | 7,2 detik |
| **Total satu request** | **26,8 detik** |
| **Memori puncak satu request** | **867 MB** |

Dua konsekuensi yang menentukan konfigurasi:

- **Timeout.** Bawaan gunicorn 30 detik. Pada mesin di atas sisa marginnya
  tinggal ~3 detik; pada mesin lebih lambat sudah terlewat. Kalau timeout
  terlampaui, gunicorn **membunuh worker-nya** — pemakai melihat kegagalan
  padahal ekstraksinya berjalan benar. Karena itu disetel 180 detik.
- **Memori dikali jumlah worker.** 867 MB itu untuk SATU request satu berkas;
  batas unggah 64 MB dan boleh banyak berkas sekaligus, jadi kasus terburuknya
  lebih besar. Empat worker berarti menyiapkan ~4 GB hanya untuk ekstraksi.
  **Setel `XR_WORKERS` menurut RAM, bukan menurut jumlah core** — ekstraksi PDF
  itu CPU-bound, jadi rumus "2 × core + 1" tidak berlaku dan worker berlebih
  hanya berebut CPU yang sama.

Titik awal yang masuk akal: **4 vCPU / 8 GB**, `XR_WORKERS=4`, lalu disesuaikan
dari pemakaian nyata.

---

## 4. Menjalankan

### Docker

```bash
docker build -t xr-app .
docker run -d --name xr-app \
  -p 127.0.0.1:5000:5000 \
  -e XR_WORKERS=4 \
  --memory=6g \
  --restart=unless-stopped \
  xr-app
```

Pemetaan `127.0.0.1:5000:5000` penting: container hanya dapat dijangkau dari
mesin itu sendiri, dan nginx yang meneruskannya. Menulis `-p 5000:5000` saja
akan membuka port itu ke seluruh jaringan.

### Tanpa Docker

```bash
pip install -r requirements.txt
XR_BIND=127.0.0.1:5000 XR_WORKERS=4 gunicorn -c gunicorn.conf.py wsgi:app
```

Jangan pernah menjalankan `python app.py` untuk melayani orang lain — itu
server pengembangan Werkzeug, dan pembuatnya sendiri menyatakan bukan untuk
produksi.

### Variabel lingkungan

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `XR_BIND` | `127.0.0.1:5000` | Alamat yang didengarkan gunicorn |
| `XR_WORKERS` | 2–4 (ikut jumlah core) | Setel menurut RAM, lihat §3 |
| `XR_TIMEOUT` | `180` | Detik. Harus ≥ timeout proxy di depannya |
| `XR_LOG_LEVEL` | `info` | |
| `XR_DEBUG` | *(mati)* | **Jangan dinyalakan di server.** Lihat §6 |

---

## 5. Reverse proxy (nginx)

```nginx
server {
    listen 443 ssl;
    server_name xr-app.internal;

    ssl_certificate     /etc/ssl/certs/xr-app.crt;
    ssl_certificate_key /etc/ssl/private/xr-app.key;

    # Samakan dengan MAX_CONTENT_LENGTH aplikasi (64 MB). Kalau lebih kecil,
    # nginx menolak lebih dulu dengan 413 dan pesan aplikasi yang sudah
    # dirancang jelas tidak pernah sampai ke pemakainya.
    client_max_body_size 64m;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # HARUS >= timeout gunicorn, kalau tidak yang memutus duluan adalah
        # nginx — dan worker gunicorn terus bekerja untuk request yang
        # koneksinya sudah tidak ada.
        proxy_read_timeout    180s;
        proxy_send_timeout    180s;
        proxy_request_buffering off;   # unggahan besar tidak ditahan dulu
    }
}
```

---

## 6. Daftar periksa sebelum melayani pemakai

- [ ] `XR_DEBUG` **tidak** disetel. Saat debug menyala, halaman error Werkzeug
      menampilkan potongan kode sumber dan **isi variabel lokal** — pada
      aplikasi ini itu berarti nama pemilik rekening, nomor rekening, dan baris
      mutasinya. Debugger-nya terkunci PIN, halaman tracebacknya tidak.
- [ ] Dijalankan lewat gunicorn, bukan `python app.py`.
- [ ] Tidak terjangkau dari internet — hanya jaringan kantor atau VPN.
- [ ] TLS aktif di nginx.
- [ ] `timeout` gunicorn ≤ `proxy_read_timeout` nginx.
- [ ] Memori container/VM ≥ `XR_WORKERS` × ~1 GB.
- [ ] **Autentikasi sudah terpasang.** Tanpa ini, siapa pun yang bisa
      menjangkau portnya dapat mengunggah dan mengunduh rekening koran —
      termasuk di jaringan kantor bersama.
- [ ] Folder `exports/` dari versi lama sudah dihapus. Versi sekarang tidak
      pernah menulis laporan ke disk, tapi berkas lama tidak ikut terhapus
      sendiri.
- [ ] `uploads/` kosong saat idle. Isinya dihapus di blok `finally` tiap
      request; kalau menumpuk, ada proses yang mati di tengah jalan.

---

## 7. Yang belum ada

Lihat `RINGKASAN_APLIKASI.md` §6.2. Yang paling berpengaruh pada deployment:

- **Audit log** — belum ada catatan siapa mengunggah apa.
- **Job queue** — setelah dipakai, request web tidak lagi menunggu ekstraksi,
  `XR_TIMEOUT` bisa turun kembali ke puluhan detik, dan hasil ekstraksi perlu
  kebijakan retensi tersendiri karena worker dan pengunduh menjadi proses yang
  berbeda.
