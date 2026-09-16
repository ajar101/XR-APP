"""
antrean.py — Sambungan ke Redis/RQ, dan keputusan mode kerja.

XR-App bisa berjalan dalam dua mode, dan yang menentukan hanya satu hal:
apakah `XR_REDIS_URL` disetel.

  TIDAK disetel  → MODE LANGSUNG. Ekstraksi dikerjakan di dalam request,
                   seperti sejak awal. Cocok untuk pemasangan satu cabang
                   dengan sedikit pemakai: tidak perlu Redis, tidak perlu
                   proses worker terpisah, dan laporan tidak pernah
                   meninggalkan memori proses.

  DISETEL        → MODE ANTREAN. Request web hanya menerima berkas lalu
                   menitipkan pekerjaannya; worker terpisah yang mengerjakan.
                   Perlu begitu ekstraksi 30–90 detik mulai menabrak timeout
                   proxy atau membuat pemakai lain mengantre.

Keduanya memakai jalur ekstraksi yang SAMA (tugas.proses_ekstraksi), jadi
berpindah mode tidak mengubah isi laporan — hanya mengubah siapa yang
mengerjakannya dan bagaimana pemakai menunggunya.

Mode antrean memerlukan Redis. Kalau `XR_REDIS_URL` disetel tapi Redis tidak
bisa dihubungi, aplikasi MENOLAK start — bukan diam-diam kembali ke mode
langsung. Kembali diam-diam berarti pemakai mengira pekerjaannya diantre
padahal koneksinya ditahan, dan itu jenis kejutan yang paling buruk saat
produksi sedang sibuk.
"""

import os

NAMA_ANTREAN = 'xr-ekstraksi'

# Batas waktu satu job dianggap gantung dan dibatalkan RQ. Dilonggarkan jauh
# di atas pengukuran (PDF 257 halaman = 27 detik) karena batas unggah 64 MB
# dan boleh banyak berkas sekaligus.
BATAS_JOB_DETIK = int(os.environ.get('XR_BATAS_JOB', '900'))


def url_redis():
    """URL Redis, atau None kalau aplikasi berjalan dalam mode langsung."""
    return os.environ.get('XR_REDIS_URL') or None


def mode_antrean() -> bool:
    return url_redis() is not None


def koneksi_redis():
    """
    Koneksi Redis. Melempar exception kalau tidak bisa dihubungi — lihat
    catatan di kepala berkas soal kenapa tidak jatuh ke mode langsung.
    """
    from redis import Redis
    conn = Redis.from_url(url_redis())
    conn.ping()
    return conn


def antrean():
    from rq import Queue
    return Queue(NAMA_ANTREAN, connection=koneksi_redis(),
                 default_timeout=BATAS_JOB_DETIK)


def periksa_sambungan(logger=None) -> None:
    """
    Dipanggil sekali saat aplikasi start. Gagal di sini jauh lebih baik
    daripada gagal pada unggahan pertama pemakai.
    """
    if not mode_antrean():
        if logger:
            logger.info('Mode LANGSUNG: ekstraksi dikerjakan di dalam request. '
                        'Setel XR_REDIS_URL untuk memakai antrean.')
        return
    try:
        koneksi_redis()
    except Exception as e:
        raise RuntimeError(
            f'XR_REDIS_URL disetel ({url_redis()}) tapi Redis tidak bisa '
            f'dihubungi: {e}\n'
            'Jalankan Redis-nya, atau hapus XR_REDIS_URL untuk kembali ke '
            'mode langsung. Lihat DEPLOY.md.'
        ) from e
    if logger:
        logger.info('Mode ANTREAN: ekstraksi dititipkan ke worker lewat %s. '
                    'Jalankan worker dengan: python worker.py', url_redis())
