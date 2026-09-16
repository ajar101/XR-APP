"""
worker.py — Proses pekerja yang mengerjakan ekstraksi dari antrean.

    python worker.py

Jalankan sebagai proses TERPISAH dari gunicorn, dan sebanyak yang muat di
memori mesin: tiap worker mengerjakan satu ekstraksi pada satu waktu, dan
satu ekstraksi PDF 257 halaman memuncak di ~870 MB.

Worker hanya perlu dijalankan kalau `XR_REDIS_URL` disetel. Tanpa itu
aplikasi berjalan dalam mode langsung dan tidak ada yang mengantre — lihat
antrean.py.

Worker TIDAK mengimpor app.py. Ia hanya butuh jalur ekstraksi (tugas.py) dan
penulis jejak audit yang tidak bergantung konteks Flask
(auth.catat_audit_langsung), jadi ia tidak ikut membawa server web,
konfigurasi sesi, maupun kunci rahasianya.
"""

import logging
import os
import sys

SESAT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SESAT)

import antrean  # noqa: E402
import tugas    # noqa: E402


def main() -> int:
    logging.basicConfig(
        level=os.environ.get('XR_LOG_LEVEL', 'INFO').upper(),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    log = logging.getLogger('worker')

    if not antrean.mode_antrean():
        sys.exit(
            'XR_REDIS_URL belum disetel, jadi tidak ada antrean yang perlu '
            'dikerjakan.\nAplikasi sedang berjalan dalam mode langsung '
            '(ekstraksi di dalam request). Lihat DEPLOY.md.')

    from rq import Worker

    koneksi = antrean.koneksi_redis()

    # Sapu folder unggahan yatim saat worker start: kalau worker sebelumnya
    # mati mendadak (OOM, mesin restart), blok `finally`-nya tidak sempat
    # jalan dan PDF rekening nasabah bisa tertinggal di disk.
    folder = os.environ.get('XR_UPLOAD_DIR', os.path.join(SESAT, 'uploads'))
    tugas.sapu_yatim(folder)

    log.info('Worker siap. Antrean=%s Redis=%s Unggahan=%s TTL hasil=%d detik',
             antrean.NAMA_ANTREAN, antrean.url_redis(), folder,
             tugas.TTL_HASIL_DETIK)

    Worker([antrean.NAMA_ANTREAN], connection=koneksi).work(with_scheduler=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
