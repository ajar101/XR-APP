# XR-App — citra produksi.
#
#   docker build -t xr-app .
#   docker run --rm -p 127.0.0.1:5000:5000 xr-app
#
# Lihat DEPLOY.md untuk topologi lengkapnya (reverse proxy, TLS, jaringan).

FROM python:3.11-slim

# pdfplumber/pdfminer.six murni Python, tapi butuh beberapa pustaka sistem
# untuk membaca gambar di dalam PDF.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libjpeg62-turbo zlib1g \
 && rm -rf /var/lib/apt/lists/*

# Berjalan sebagai pengguna biasa, bukan root: aplikasi ini memproses
# berkas yang datang dari luar, jadi jangan beri proses yang menguraikannya
# hak lebih dari yang dibutuhkan.
RUN useradd --create-home --uid 10001 xrapp

WORKDIR /app

# Dependensi disalin lebih dulu supaya lapisan ini ikut cache dan tidak
# dipasang ulang tiap kali kode berubah.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=xrapp:xrapp . .

# PDF yang diunggah singgah di sini sebelum diuraikan, lalu dihapus di blok
# `finally`. Laporan Excel-nya tidak pernah menyentuh disk sama sekali.
RUN mkdir -p /app/uploads && chown xrapp:xrapp /app/uploads

USER xrapp

# Di dalam container mengikat ke semua antarmuka supaya bisa dipetakan
# keluar; yang membatasi jangkauan adalah pemetaan port dan jaringan di
# luar container (lihat DEPLOY.md), bukan setelan ini.
ENV XR_BIND=0.0.0.0:5000 \
    PYTHONUNBUFFERED=1
EXPOSE 5000

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
