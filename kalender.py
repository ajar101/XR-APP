"""
kalender.py — Hari kerja bank, dipakai bersama engine dan extractor.

KENAPA BERKAS TERSENDIRI

`engine/` dan `extractors/` sengaja tidak saling impor: aturan bank dimiliki
extractor, dan engine tidak boleh "tahu" ketentuan bank mana pun (lihat
docstring `_check_jadwal_biaya_adm`). Tapi kalender hari libur bukan milik
siapa-siapa — ia fakta nasional yang dipakai keduanya.

Ditaruh di sini supaya tidak ada yang menyalinnya. Duplikasi daftar libur
persis jenis kesalahan yang sudah pernah dikoreksi di proyek ini, waktu
aturan BCA masih tinggal di dalam engine dan diam-diam ikut diberlakukan ke
bank lain.

YANG DAFTAR INI TIDAK MUAT

Hanya libur nasional bertanggal TETAP. Libur berbasis kalender lunar/hijriah
— Lebaran, Nyepi, Imlek, Waisak, dan seterusnya — tanggalnya berubah tiap
tahun dan butuh referensi eksternal yang belum ada di aplikasi ini. Jadi
`hari_bank_pertama` bisa mengembalikan tanggal yang ternyata libur, dan itu
diterima sadar: yang dihasilkannya temuan yang perlu dibaca manusia, bukan
angka laporan yang salah diam-diam.
"""

import datetime

# Hari libur nasional tanggal-tetap (tidak mencakup libur berbasis kalender
# lunar/hijriah seperti Lebaran, Nyepi, Imlek, dst — tanggalnya berubah tiap
# tahun dan butuh referensi eksternal). Cukup untuk menyaring kasus paling
# jelas; jangan dibaca sebagai daftar libur yang lengkap.
LIBUR_TANGGAL_TETAP = {
    (1, 1),   # Tahun Baru Masehi
    (5, 1),   # Hari Buruh
    (8, 17),  # Kemerdekaan RI
    (12, 25), # Natal
}


def hari_libur(tgl: datetime.date) -> bool:
    """Akhir pekan atau libur nasional bertanggal tetap."""
    return tgl.weekday() >= 5 or (tgl.month, tgl.day) in LIBUR_TANGGAL_TETAP


def hari_bank_pertama(tahun: int, bulan: int, maks_geser: int = 6):
    """
    Tanggal hari kerja bank pertama di bulan itu.

    Akhir pekan dan libur nasional bertanggal tetap dilompati. Dua dari empat
    libur tetap justru jatuh tanggal 1 — Tahun Baru dan Hari Buruh — jadi
    mengabaikannya bukan kasus pinggiran: dari 2026 sampai 2030, delapan dari
    sepuluh kejadian 1 Januari/1 Mei jatuh di hari kerja, dan di situlah
    liburnya benar-benar menggeser pendebetan.

    `maks_geser` membatasi pencarian supaya daftar libur yang suatu saat
    diperluas tidak bisa membuat fungsi ini berjalan sampai keluar bulan.
    Mengembalikan None kalau tahun/bulannya tidak valid atau batas itu
    terlampaui — pemanggilnya yang memutuskan, dan yang benar biasanya
    MELEWATKAN pemeriksaan alih-alih menebak.
    """
    try:
        tgl = datetime.date(tahun, bulan, 1)
    except (ValueError, OverflowError):
        return None
    for _ in range(maks_geser + 1):
        if not hari_libur(tgl):
            return tgl.day
        tgl += datetime.timedelta(days=1)
        if tgl.month != bulan:
            return None
    return None
