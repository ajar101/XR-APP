"""
mestika.py — Dispatcher untuk berbagai format rekening Bank Mestika.

Auto-detect format PDF Mestika dan delegate ke extractor yang sesuai:
  - 'statement' : REKENING KORAN / ACCOUNT STATEMENT (e-statement PT Bank
                  Mestika Dharma Tbk, tabel lebar-tetap Tgl … Saldo)
                                                             → didukung
  - lainnya     : belum ada extractor-nya

Format Mestika lain (mis. cetakan teller di kantor cabang) ditolak di sini
dengan pesan yang menyebut format apa yang didukung. Menyerahkannya ke
extractor yang tata letaknya berbeda tidak menghasilkan penolakan,
melainkan ekstraksi kosong yang baru meledak di pembuat Excel — kegagalan
yang jauh dari sebabnya dan tidak memberi tahu apa pun ke pemakainya.
"""

import pdfplumber

from extractors.base import BaseExtractor
from extractors.mestika_statement import MestikaStatementExtractor


class MestikaExtractor(BaseExtractor):
    """Extractor Bank Mestika dengan deteksi format otomatis."""

    def __init__(self, pdf_path: str):
        super().__init__(pdf_path)
        self.format_type = self._detect_format()

        if self.format_type == 'statement':
            self.extractor = MestikaStatementExtractor(pdf_path)
        else:
            raise NotImplementedError(
                "PDF ini tidak terbaca sebagai rekening koran Bank Mestika yang "
                "didukung saat ini, yaitu \"Rekening Koran / Account Statement\" "
                "dengan kolom Tgl (Date), Keterangan (Description), Cbg (Brch), "
                "Debet/Kredit (Debit/Credit), dan Saldo (Balance). Pastikan "
                "PDF-nya e-statement yang diterbitkan langsung oleh PT Bank "
                "Mestika Dharma Tbk."
            )

    def _detect_format(self) -> str:
        """
        Kenali format dari halaman-halaman awal.

        Yang dicari judul laporannya SEKALIGUS nama-nama kolom tabelnya.
        Judul saja tidak cukup: "Account Statement" dipakai hampir semua
        bank — Laporan Rekening Koran Bank Mandiri pun bertajuk
        "(Account Statement Report)". Nama kolom saja juga tidak cukup,
        karena "Saldo"/"Balance" ada di mana-mana. Yang khas Mestika adalah
        pasangan kolom "Cbg"/"Brch" (kode cabang tiga huruf) bersama judul
        dwibahasa "Rekening Koran / Account Statement".

        Nama kolomnya dicetak dua kali per huruf untuk meniru cetak tebal
        ("CCbbgg"), jadi dicocokkan setelah hurufnya dirapatkan kembali.
        """
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                for page in pdf.pages[:5]:
                    teks = (page.extract_text() or '')
                    atas = teks.upper()
                    rapat = self._rapatkan(atas)
                    if ('REKENING KORAN' in atas
                            and 'ACCOUNT STATEMENT' in atas
                            and 'DESCRIPTION' in rapat
                            and 'BRCH' in rapat
                            and 'BALANCE' in rapat):
                        return 'statement'
        except Exception:
            # PDF-nya sendiri tidak bisa dibuka: biarkan alur ekstraksi
            # normal yang melaporkan errornya, jangan salah diagnosis di sini.
            return 'tidak dikenal'
        return 'tidak dikenal'

    @staticmethod
    def _rapatkan(teks: str) -> str:
        """"DDeessccrriippttiioonn" → "DESCRIPTION"."""
        import re
        return re.sub(r'(.)\1', r'\1', teks)

    def get_file_prefix(self) -> str:
        return 'MESTIKA'

    def extract_saldo(self) -> dict:
        return self.extractor.extract_saldo()

    def extract_transaksi(self) -> dict:
        return self.extractor.extract_transaksi()

    def extract_no_rekening(self) -> str:
        return self.extractor.extract_no_rekening()

    def validate(self) -> dict:
        """
        Teruskan checksum sub-extractor.

        Tanpa delegasi ini app.py hanya melihat dispatcher, yang tidak punya
        validate(), sehingga pemeriksaan checksum diam-diam tidak pernah jalan.
        """
        return self.extractor.validate()
