"""
gunicorn.conf.py — Konfigurasi server produksi XR-App.

    gunicorn -c gunicorn.conf.py wsgi:app

Angka-angka di sini bukan bawaan gunicorn. Ekstraksi PDF rekening koran
berperilaku sangat berbeda dari request web biasa, dan dua bawaan gunicorn
justru mematikan aplikasi ini kalau dibiarkan — lihat catatan tiap setelan.
"""

import multiprocessing
import os

bind = os.environ.get('XR_BIND', '127.0.0.1:5000')

# ── Timeout ────────────────────────────────────────────────────────────
# Bawaan gunicorn 30 detik, dan itu TERLALU PENDEK untuk aplikasi ini.
# Diukur pada PDF referensi terbesar (Mandiri Kopra, 257 halaman, 5 MB):
# ekstraksi 19,6 detik + penyusunan Excel 7,2 detik = 26,8 detik — sudah
# nyaris menyentuh batas pada mesin cepat, dan lewat pada mesin yang lebih
# lambat. Batas upload aplikasi 64 MB dan boleh banyak berkas sekaligus,
# jadi kasus terburuknya jauh di atas itu.
#
# Kalau timeout terlampaui, gunicorn MEMBUNUH worker-nya: pemakai melihat
# kegagalan padahal ekstraksinya berjalan benar. Karena itu dilonggarkan.
# Nilai ini harus >= timeout proxy di depannya (lihat DEPLOY.md), kalau
# tidak yang memutus duluan adalah proxy-nya.
#
# Setelah job queue dipakai (RQ), request web tidak lagi menunggu
# ekstraksi dan angka ini bisa diturunkan kembali ke puluhan detik.
timeout = int(os.environ.get('XR_TIMEOUT', '180'))
graceful_timeout = 30
keepalive = 5

# ── Worker ─────────────────────────────────────────────────────────────
# Ekstraksi PDF itu CPU-bound (pdfplumber), bukan I/O-bound, jadi rumus
# lazim "2 x core + 1" tidak berlaku — worker berlebih hanya berebut CPU
# yang sama dan memperlambat semuanya.
#
# Yang lebih menentukan justru MEMORI: satu request PDF 257 halaman
# memuncak di ~870 MB. Tiap worker menangani satu request pada satu waktu,
# jadi kebutuhan memori = jumlah worker x puncak per request. Empat worker
# berarti menyiapkan ~4 GB hanya untuk ekstraksi.
#
# Setel XR_WORKERS sesuai RAM mesinnya, jangan sesuai jumlah core.
workers = int(os.environ.get('XR_WORKERS', max(2, min(4, multiprocessing.cpu_count()))))
worker_class = 'sync'

# Daur ulang worker secara berkala. pdfplumber menyisakan memori pada
# dokumen besar; tanpa ini pemakaian memori proses naik terus sampai
# kernel mematikannya di tengah pekerjaan orang.
max_requests = 100
max_requests_jitter = 20

# ── Log ────────────────────────────────────────────────────────────────
accesslog = os.environ.get('XR_ACCESS_LOG', '-')
errorlog = os.environ.get('XR_ERROR_LOG', '-')
loglevel = os.environ.get('XR_LOG_LEVEL', 'info')

# Query string TIDAK dicatat di access log. Aplikasi ini tidak menaruh data
# rekening di URL, tapi log akses cenderung dikirim ke agregator dan
# disimpan lama — jangan sampai jadi jalan bocor kalau kelak ada parameter
# baru yang memuat nomor rekening.
access_log_format = '%(h)s "%(m)s %(U)s" %(s)s %(b)s %(M)sms "%(a)s"'
