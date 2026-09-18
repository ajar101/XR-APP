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
docker volume create xr-app-data

docker run -d --name xr-app \
  -p 127.0.0.1:5000:5000 \
  -v xr-app-data:/app/data \
  -e XR_SECRET_KEY="$(cat /etc/xr-app/secret_key)" \
  -e XR_WORKERS=4 \
  --memory=6g \
  --restart=unless-stopped \
  xr-app

# sekali saja, untuk membuat admin pertama
docker exec -it xr-app python manage.py buat-admin
```

Volume `xr-app-data` menyimpan akun pengguna dan jejak audit. **Tanpa volume,
keduanya hilang setiap container dibuat ulang.**

Pemetaan `127.0.0.1:5000:5000` penting: container hanya dapat dijangkau dari
mesin itu sendiri, dan nginx yang meneruskannya. Menulis `-p 5000:5000` saja
akan membuka port itu ke seluruh jaringan.

### Tanpa Docker

```bash
pip install -r requirements.txt
python manage.py buat-admin        # sekali saja
XR_SECRET_KEY=... XR_BIND=127.0.0.1:5000 XR_WORKERS=4 \
  gunicorn -c gunicorn.conf.py wsgi:app
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
| `XR_SECRET_KEY` | — | **Wajib.** Aplikasi menolak jalan tanpa ini. Lihat §4.1 |
| `XR_SECRET_FILE` | `data/secret_key` | Tempat kunci sesi disimpan **saat dijalankan di mesin sendiri**. Tidak pernah dipakai lewat gunicorn — lihat §4.1 |
| `XR_DB` | `data/xr-app.db` | Basis data pengguna & jejak audit. **Butuh volume awet** |
| `XR_REDIS_URL` | *(kosong)* | Disetel → mode antrean. Kosong → mode langsung. Lihat §4.2 |
| `XR_TTL_HASIL` | `3600` | Detik. Umur berkas hasil di mode antrean — inilah kebijakan retensinya |
| `XR_BATAS_JOB` | `900` | Detik. Batas satu job dianggap gantung |

### 4.1 Kunci sesi dan akun pertama

Buat kunci sesi **sekali saja**, lalu simpan sebagai variabel lingkungan
(bukan di dalam repositori):

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Di Windows/PowerShell:

```powershell
$env:XR_SECRET_KEY = "..."     # sesi terminal ini saja
setx XR_SECRET_KEY "..."       # tetap; berlaku di terminal yang dibuka SESUDAHNYA
```

Kunci ini menandatangani cookie sesi. Tanpa kunci yang rahasia dan **tetap**,
cookie login bisa dipalsukan. Kunci acak yang dibuat tiap kali proses mulai
juga tidak memadai: tiap worker gunicorn akan punya kunci berbeda sehingga
sesi pemakai putus bergantian, dan semua orang ter-logout tiap restart.
Karena itu di produksi ketiadaannya membuat aplikasi **menolak jalan**, bukan
diam-diam memakai kunci sementara.

#### Mencoba di mesin sendiri

Untuk sekadar mencoba di laptop, `XR_SECRET_KEY` **tidak perlu disetel**:

```bash
python app.py
```

Kuncinya dibuat sekali lalu disimpan di `data/secret_key` dengan izin `0600`,
sehingga sesi tetap awet antar restart. Letaknya bisa dipindah lewat
`XR_SECRET_FILE`.

**Jangan** memakai `XR_DEBUG=1` untuk ini. Dulu itu satu-satunya jalan
menjalankan tanpa `XR_SECRET_KEY`, dan harganya mahal: `XR_DEBUG=1`
menyalakan halaman traceback Werkzeug yang menampilkan isi variabel lokal —
pada aplikasi ini berarti nama pemilik rekening, nomor rekening, dan baris
mutasinya. Sejak berkas kunci ada, debug tidak lagi diperlukan untuk alasan
itu.

Jalan pintas ini **hanya** berlaku kalau kedua syarat terpenuhi: dijalankan
langsung sebagai `python app.py`, **dan** alamat ikatnya loopback. Lewat
gunicorn (`wsgi:app`) berkas kunci tidak pernah dibaca, dan menyetel
`XR_HOST` ke alamat yang terjangkau jaringan mengembalikan penolakan —
sekalipun berkasnya ada. Dijaga `tests/kunci_sesi.py`.

Admin pertama dibuat dari baris perintah, bukan lewat halaman pendaftaran:

```bash
python manage.py buat-admin        # di dalam container: docker exec -it xr-app python manage.py buat-admin
```

Halaman pendaftaran terbuka harus bisa diakses tanpa login, dan selama belum
ada yang mendaftar, siapa pun yang menjangkau aplikasi bisa mengangkat
dirinya jadi admin. Lewat CLI, pembuat admin pertama harus sudah punya akses
ke servernya. Pengguna berikutnya ditambahkan admin lewat halaman
**Pengguna** di aplikasi.

Kata sandi minimal 12 karakter dan tidak pernah diterima sebagai argumen
baris perintah — argumen tersimpan di riwayat shell dan terlihat proses lain
lewat daftar proses.

### 4.2 Dua mode kerja: langsung vs antrean

Yang menentukan hanya satu hal: apakah `XR_REDIS_URL` disetel.

| | **Mode langsung** (bawaan) | **Mode antrean** |
|---|---|---|
| `XR_REDIS_URL` | tidak disetel | disetel |
| Ekstraksi dikerjakan | di dalam request web | worker terpisah |
| Pemakai menunggu | koneksi ditahan 27–90 detik | jawaban seketika, lalu halaman memantau status |
| Perlu Redis & worker | tidak | ya |
| Laporan hasil | tidak pernah keluar dari memori proses | tersimpan di Redis, kedaluwarsa otomatis |
| `XR_TIMEOUT` gunicorn | harus longgar (180 dtk) | boleh kembali ke puluhan detik |

Keduanya memakai jalur ekstraksi yang **sama** (`tugas.proses_ekstraksi`), jadi
berpindah mode tidak mengubah isi laporan — hanya mengubah siapa yang
mengerjakannya dan bagaimana pemakai menunggunya.

**Mulailah dari mode langsung.** Untuk satu cabang dengan sedikit pemakai, ia
lebih sederhana dan lebih aman: tidak perlu Redis, tidak perlu proses worker,
dan laporan tidak pernah meninggalkan memori proses sehingga tidak ada yang
perlu dijadwalkan hapus.

**Pindah ke mode antrean** begitu salah satu dari ini mulai terjadi: pemakai
melihat timeout pada PDF besar, atau beberapa orang mengunggah bersamaan dan
saling mengantre.

```bash
# di mesin yang sama, atau Redis internal yang sudah ada
export XR_REDIS_URL=redis://127.0.0.1:6379/0

# jalankan minimal satu worker, TERPISAH dari gunicorn
python worker.py
```

Jumlah worker disetel seperti `XR_WORKERS` gunicorn: menurut RAM, bukan
jumlah core — tiap worker mengerjakan satu ekstraksi pada satu waktu dan satu
ekstraksi memuncak di ~870 MB.

Kalau `XR_REDIS_URL` disetel tapi Redis tidak bisa dihubungi, aplikasi
**menolak start** — bukan diam-diam kembali ke mode langsung. Kembali
diam-diam berarti pemakai mengira pekerjaannya diantre padahal koneksinya
ditahan, dan itu jenis kejutan yang paling buruk saat produksi sedang sibuk.

#### Retensi di mode antrean

Di mode antrean, worker dan pengunduh adalah proses **berbeda**, jadi laporan
hasil harus tersimpan di antara keduanya. Ia disimpan di Redis dengan umur
`XR_TTL_HASIL` (bawaan 1 jam), yang **inilah kebijakan retensinya**: Redis
sendiri yang menghapusnya, jadi tidak ada job pembersih yang bisa lupa
dijalankan. Setelah lewat, permintaan unduh dijawab 404 dengan pesan meminta
pemakai mengunggah ulang.

Karena laporan berisi data rekening ikut singgah di Redis, Redis-nya perlu
perlakuan yang sama dengan basis data: **tidak boleh terjangkau dari luar
mesin**, dan sebaiknya tanpa persistensi ke disk (`--save '' --appendonly no`)
supaya isinya tidak tertulis ke berkas dump.

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
- [ ] `XR_SECRET_KEY` disetel dari nilai yang **tetap** dan rahasia, bukan
      dibuat ulang tiap deploy (kalau berubah, semua pemakai ter-logout).
      Berkas `data/secret_key` yang mungkin ikut tersalin dari percobaan
      lokal **tidak** memenuhi syarat ini dan memang tidak akan terpakai:
      jalur gunicorn tidak pernah membacanya.
- [ ] `XR_DB` menunjuk ke volume yang awet, dan volumenya **ikut dicadangkan**.
      Isinya akun pengguna serta jejak audit — termasuk nomor rekening yang
      pernah diproses, jadi cadangannya perlu perlakuan yang sama dengan data
      nasabah lain (terenkripsi, akses terbatas, jadwal hapus).
- [ ] Admin pertama sudah dibuat (`python manage.py buat-admin`), dan tidak
      ada akun contoh/uji yang tertinggal aktif.
- [ ] Folder `exports/` dari versi lama sudah dihapus. Versi sekarang tidak
      pernah menulis laporan ke disk, tapi berkas lama tidak ikut terhapus
      sendiri.
- [ ] `uploads/` kosong saat idle. Isinya dihapus di blok `finally` tiap
      request; kalau menumpuk, ada proses yang mati di tengah jalan. (Folder
      yatim tetap disapu otomatis setelah 6 jam — lihat `tugas.sapu_yatim`.)
- [ ] **Bila memakai mode antrean:** minimal satu `worker.py` berjalan, Redis
      tidak terjangkau dari luar mesin, dan persistensi Redis dimatikan.

---

## 7. Yang belum ada

Lihat `RINGKASAN_APLIKASI.md` §6.2. Yang paling berpengaruh pada deployment:

- **Histori hasil ekstraksi** — jejak audit mencatat *bahwa* sebuah rekening
  diproses, tapi hasilnya sendiri tidak disimpan. Begitu disimpan, isolasi
  antar cabang yang sekarang hanya berlaku untuk jejak audit tinggal dipakai
  ulang untuk data hasilnya.
