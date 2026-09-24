"""
mandiri.py — Dispatcher untuk berbagai format rekening Mandiri.

Auto-detect format PDF Mandiri dan delegate ke extractor yang sesuai:
  - 'kopra'      : Kopra by Mandiri                          → didukung
  - 'estatement' : e-Statement (Livin'/Mandiri Online)       → didukung
  - 'koran'      : Laporan Rekening Koran (Account Statement
                   Report, tabel bergaris)                   → didukung
  - 'ebanking'   : Mandiri E-Banking                         → belum ada extractor

Kunci formatnya sengaja dibedakan 'estatement' vs 'koran'. Keduanya sempat
memakai nama 'statement' yang sama, sehingga PDF Rekening Koran ikut
diarahkan ke extractor e-Statement — hasilnya bukan penolakan yang jelas,
melainkan ekstraksi kosong yang baru meledak di pembuat Excel (HTTP 500).
Format yang belum didukung harus gagal cepat dengan pesan yang menyebut
formatnya, bukan gagal jauh di hilir.

Satu PDF boleh memuat beberapa format sekaligus (mis. Kopra, e-Statement,
dan Rekening Koran untuk rekening yang sama). Format dikenali per halaman;
kalau ada lebih dari satu, tiap segmen diurai extractor formatnya sendiri
lalu digabung oleh extractors/campuran.py.
"""

import pdfplumber
from extractors.base import BaseExtractor
from extractors.mandiri_kopra import MandiriKopraExtractor
from extractors.mandiri_statement import MandiriStatementExtractor
from extractors.mandiri_koran import MandiriKoranExtractor
from extractors.campuran import bangun_extractor

KELAS_FORMAT = {
    'kopra': MandiriKopraExtractor,
    'estatement': MandiriStatementExtractor,
    'koran': MandiriKoranExtractor,
}
NAMA_FORMAT = {
    'kopra': 'Kopra by Mandiri',
    'estatement': 'e-Statement',
    'koran': 'Laporan Rekening Koran',
}

# Import akan ditambahkan saat format lain sudah dibuat:
# from extractors.mandiri_ebanking import MandiriEBankingExtractor


class MandiriExtractor(BaseExtractor):
    """
    Main Mandiri extractor dengan auto-detection.
    Mendeteksi format PDF dan delegate ke sub-extractor yang sesuai.
    """

    def __init__(self, pdf_path: str):
        super().__init__(pdf_path)
        # Auto-detect format
        self.format_type = self._detect_format()
        
        # Delegate to appropriate sub-extractor
        if self.format_type not in KELAS_FORMAT:
            nama_format = {
                'ebanking': 'E-Banking',
            }.get(self.format_type, self.format_type)
            raise NotImplementedError(
                f"PDF terdeteksi sebagai format Mandiri {nama_format}, yang belum "
                f"didukung. Saat ini yang tersedia: Kopra by Mandiri, e-Statement "
                f"(Livin'/Mandiri Online), dan Laporan Rekening Koran."
            )

        self.format_type, self.extractor = bangun_extractor(
            pdf_path, self.format_type, KELAS_FORMAT, NAMA_FORMAT,
            self._format_halaman, prefix='MANDIRI', toleransi=0.005)

    @staticmethod
    def _format_halaman(teks: str):
        """
        Format satu halaman, untuk memecah PDF yang memuat beberapa format.

        Kopra dan e-Statement mencetak penandanya di SETIAP halaman; Rekening
        Koran hanya di halaman pertama tiap laporan — halaman lanjutannya
        tidak bertanda dan ikut format halaman sebelumnya (lihat
        campuran.pecah_segmen). Urutannya sama dengan _detect_format().
        """
        u = teks.upper()
        if 'KOPRA BY MANDIRI' in u or 'KOPRABYMANDIRI.COM' in u:
            return 'kopra'
        if 'E-STATEMENT' in u:
            return 'estatement'
        if 'REKENING KORAN' in u:
            return 'koran'
        return None

    def _detect_format(self) -> str:
        """
        Detect Mandiri format from PDF content.
        
        Returns:
            'kopra', 'estatement', 'koran', atau 'ebanking'
        """
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                if not pdf.pages:
                    return 'kopra'  # default fallback

                # Check first page
                text = pdf.pages[0].extract_text() or ''
                text_upper = text.upper()

                # Detection keywords
                if 'KOPRA BY MANDIRI' in text_upper or 'KOPRABYMANDIRI.COM' in text_upper:
                    return 'kopra'

                # e-Statement (Livin'/Mandiri Online). Dicek SEBELUM
                # 'E-BANKING'/'LIVIN' karena halaman disclaimer e-Statement
                # menyebut "Livin'" juga — kalau urutannya dibalik, format
                # yang sudah didukung malah dianggap format yang belum ada.
                if 'E-STATEMENT' in text_upper and 'SALDO AWAL' in text_upper:
                    return 'estatement'

                # Laporan Rekening Koran (Account Statement Report).
                if 'REKENING KORAN' in text_upper:
                    return 'koran'

                if 'E-BANKING' in text_upper or 'LIVIN' in text_upper:
                    return 'ebanking'

                # Default to kopra if uncertain (most common format)
                return 'kopra'

        except Exception:
            return 'kopra'  # safe default
    
    def get_file_prefix(self) -> str:
        """Return 'MANDIRI' as file prefix."""
        return 'MANDIRI'
    
    def extract_saldo(self) -> dict:
        """Delegate to sub-extractor."""
        return self.extractor.extract_saldo()
    
    def extract_transaksi(self) -> dict:
        """Delegate to sub-extractor."""
        return self.extractor.extract_transaksi()
    
    def extract_no_rekening(self) -> str:
        """Delegate to sub-extractor."""
        return self.extractor.extract_no_rekening()

    def validate(self) -> dict:
        """
        Teruskan checksum sub-extractor.

        Tanpa delegasi ini app.py hanya melihat dispatcher, yang tidak punya
        validate(), sehingga pemeriksaan checksum diam-diam tidak pernah jalan.
        """
        return self.extractor.validate()
