"""
mandiri.py — Dispatcher untuk berbagai format rekening Mandiri.

Auto-detect format PDF Mandiri dan delegate ke extractor yang sesuai:
  - Kopra by Mandiri
  - Mandiri E-Banking / Livin
  - Mandiri Statement (format lama)
"""

import pdfplumber
from extractors.base import BaseExtractor
from extractors.mandiri_kopra import MandiriKopraExtractor

# Import akan ditambahkan saat format lain sudah dibuat:
# from extractors.mandiri_ebanking import MandiriEBankingExtractor
# from extractors.mandiri_statement import MandiriStatementExtractor


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
        elif self.format_type == 'ebanking':
            # Placeholder: akan diimplementasikan nanti
            raise NotImplementedError(
                "Mandiri E-Banking format belum didukung. "
                "Saat ini hanya Kopra by Mandiri yang tersedia."
            )
        else:  # statement
            # Placeholder: akan diimplementasikan nanti
            raise NotImplementedError(
                "Mandiri Statement format belum didukung. "
                "Saat ini hanya Kopra by Mandiri yang tersedia."
            )
    
    def _detect_format(self) -> str:
        """
        Detect Mandiri format from PDF content.
        
        Returns:
            'kopra', 'ebanking', or 'statement'
        """
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                if not pdf.pages:
                    return 'statement'  # default fallback
                
                # Check first page
                text = pdf.pages[0].extract_text() or ''
                text_upper = text.upper()
                
                # Detection keywords
                if 'KOPRA BY MANDIRI' in text_upper or 'KOPRABYMANDIRI.COM' in text_upper:
                    return 'kopra'
                
                if 'E-BANKING' in text_upper or 'LIVIN' in text_upper:
                    return 'ebanking'
                
                # Check for old statement format markers
                if 'BANK MANDIRI' in text_upper and 'REKENING KORAN' in text_upper:
                    return 'statement'
                
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
