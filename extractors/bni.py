"""
bni.py — Dispatcher untuk berbagai format rekening BNI.

Auto-detect format PDF BNI dan delegate ke extractor yang sesuai:
  - 'statement' : ACCOUNT STATEMENT (rekening giro/CURRENT, tabel bergaris
                  Posting Date … Balance)                    → didukung
  - lainnya     : belum ada extractor-nya

Format yang belum didukung ditolak di sini dengan pesan yang menyebut apa
yang terbaca dari dokumennya. Sebelumnya satu extractor dipakai untuk PDF
BNI apa pun; PDF bertata letak lain tidak ditolak, melainkan menghasilkan
ekstraksi kosong yang baru meledak di pembuat Excel — kegagalan yang jauh
dari sebabnya dan tidak memberi tahu apa pun ke pemakainya.
"""

import pdfplumber

from extractors.base import BaseExtractor
from extractors.bni_statement import BNIStatementExtractor


class BNIExtractor(BaseExtractor):
    """Extractor BNI dengan deteksi format otomatis."""

    def __init__(self, pdf_path: str):
        super().__init__(pdf_path)
        self.format_type = self._detect_format()

        if self.format_type == 'statement':
            self.extractor = BNIStatementExtractor(pdf_path)
        else:
            raise NotImplementedError(
                "PDF ini terbaca sebagai dokumen BNI, tapi tata letaknya bukan "
                "format ACCOUNT STATEMENT yang didukung saat ini (tabel bergaris "
                "dengan kolom Posting Date, Journal, Transaction Description, "
                "Amount, DB/CR, Balance). Pastikan PDF-nya diunduh sebagai "
                "Account Statement dari BNI Direct/BNI iBank."
            )

    def _detect_format(self) -> str:
        """
        Kenali format dari halaman-halaman awal.

        Yang dicari bukan judul laporannya, melainkan nama-nama kolom yang
        hanya dipakai tata letak ini: "Effective Date" dan "DB/CR". Judulnya
        saja tidak cukup — Laporan Rekening Koran Bank Mandiri pun bertajuk
        "(Account Statement Report)" dan berkolom "Posting Date", sehingga
        dokumen bank lain ikut lolos dan diserahkan ke extractor yang tata
        letaknya sama sekali berbeda.
        """
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                for page in pdf.pages[:3]:
                    teks = (page.extract_text() or '').upper()
                    if ('ACCOUNT STATEMENT' in teks
                            and 'EFFECTIVE DATE' in teks
                            and 'DB/CR' in teks):
                        return 'statement'
        except Exception:
            # PDF-nya sendiri tidak bisa dibuka: biarkan alur ekstraksi
            # normal yang melaporkan errornya, jangan salah diagnosis di sini.
            return 'tidak dikenal'
        return 'tidak dikenal'

    def get_file_prefix(self) -> str:
        return 'BNI'

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
