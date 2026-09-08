"""
peringatan.py — Pencatatan "peringatan pembacaan dokumen" untuk semua extractor.

Peringatan di sini BUKAN soal kecocokan angka (itu tugas checksum lewat
metadata '_checksum'), melainkan soal kondisi dokumennya: halaman yang bukan
bagian rekening yang diperiksa, rentang tanggal yang tidak dicakup laporan
mana pun, rantai saldo yang putus.

Tiap peringatan perlu ada dalam dua bentuk:
  - teks, untuk validate()['warnings'] yang dibaca pemanggil/log;
  - terstruktur, untuk metadata '_peringatan' yang dirender engine sebagai
    baris di Sheet Indikasi Kejanggalan (lihat kontrak di extractors/base.py).

Keduanya dicatat dari SATU pemanggilan supaya isinya tidak bisa berbeda, dan
modul ini dipakai bersama supaya extractor baru tidak perlu menulis ulang
mekanismenya — cukup memanggil self._catat(...).
"""

TINGKAT_SAH = ('Tinggi', 'Sedang', 'Rendah')


def _entri(tingkat: str, ringkas: str, detail: str, bulan: str, halaman: str) -> dict:
    if tingkat not in TINGKAT_SAH:
        raise ValueError(
            f"tingkat peringatan '{tingkat}' tidak dikenal; "
            f"pakai salah satu dari {TINGKAT_SAH}"
        )
    return {'tingkat': tingkat, 'ringkas': ringkas, 'detail': detail,
            'bulan': bulan, 'halaman': halaman}


class PencatatPeringatan:
    """
    Mixin pencatat peringatan untuk extractor.

    Extractor yang memakainya wajib menyiapkan dua daftar di __init__:

        self.warnings: list[str] = []
        self.peringatan: list[dict] = []
    """

    def _catat(self, tingkat: str, ringkas: str, detail: str = '',
               bulan: str = '-', halaman: str = '-') -> None:
        """Catat peringatan yang diketahui SAAT PARSING (sekali per dokumen)."""
        self.warnings.append(f'{ringkas} {detail}'.strip())
        self.peringatan.append(_entri(tingkat, ringkas, detail, bulan, halaman))


def pencatat_laporan(report: dict):
    """
    Kembalikan fungsi pencatat untuk peringatan yang baru diketahui SAAT
    validate() berjalan.

    Ditulis ke report, bukan ke extractor, karena validate() bisa dipanggil
    lebih dari sekali — app.py memanggilnya, lalu extract_saldo() memanggilnya
    lagi untuk menyusun metadata. Kalau ditumpuk di objek extractor-nya,
    laporan akan memuat peringatan yang sama berkali-kali.
    """
    report.setdefault('warnings', [])
    report.setdefault('peringatan', [])

    def catat(tingkat: str, ringkas: str, detail: str = '',
              bulan: str = '-', halaman: str = '-') -> None:
        report['warnings'].append(f'{ringkas} {detail}'.strip())
        report['peringatan'].append(_entri(tingkat, ringkas, detail, bulan, halaman))

    return catat
