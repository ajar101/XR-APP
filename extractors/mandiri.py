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
"""

import pdfplumber
from extractors.base import BaseExtractor
from extractors.mandiri_kopra import MandiriKopraExtractor
from extractors.mandiri_statement import MandiriStatementExtractor
from extractors.mandiri_koran import MandiriKoranExtractor

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
        if self.format_type == 'kopra':
            self.extractor = MandiriKopraExtractor(pdf_path)
        elif self.format_type == 'estatement':
            self.extractor = MandiriStatementExtractor(pdf_path)
        elif self.format_type == 'koran':
            self.extractor = MandiriKoranExtractor(pdf_path)
        else:
            nama_format = {
                'ebanking': 'E-Banking',
            }.get(self.format_type, self.format_type)
            raise NotImplementedError(
                f"PDF terdeteksi sebagai format Mandiri {nama_format}, yang belum "
                f"didukung. Saat ini yang tersedia: Kopra by Mandiri, e-Statement "
                f"(Livin'/Mandiri Online), dan Laporan Rekening Koran."
            )
    
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
