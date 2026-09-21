"""
registry.py — Daftar bank yang tersedia di aplikasi.

Untuk menambah bank baru:
  1. Buat file extractor baru di extractors/<nama_bank>.py
  2. Import kelasnya di sini
  3. Tambahkan entry di BANK_REGISTRY (termasuk 'formats')

Tidak perlu mengubah file lain (app.py, engine, UI) sama sekali.

'formats' mendaftar varian tata letak yang benar-benar punya extractor untuk
bank tersebut. Halaman depan menghitungnya untuk menyebut berapa format yang
didukung; dipakai juga sebagai daftar tertulis supaya "format apa saja yang
jalan" tidak cuma tersimpan di dispatcher masing-masing bank.
"""

from extractors.bca import BCAExtractor
from extractors.mandiri import MandiriExtractor
from extractors.bni import BNIExtractor
from extractors.bri import BRIExtractor
from extractors.mestika import MestikaExtractor

BANK_REGISTRY = {
    'bca': {
        'name':        'Bank Central Asia (BCA)',
        'short_name':  'BCA',
        'extractor':   BCAExtractor,
        'color':       '#005BAA',   # biru BCA — untuk UI
        'logo_text':   'BCA',       # placeholder sebelum ada asset logo
        'description': 'Rekening Giro & Tabungan BCA',
        'formats':     ['e-Statement / Rekening Koran'],
        'enabled':     True,
    },
    'mandiri': {
        'name':        'Bank Mandiri',
        'short_name':  'MANDIRI',
        'extractor':   MandiriExtractor,
        'color':       '#003D7C',   # biru Mandiri
        'logo_text':   'MDR',       # logo placeholder
        'description': 'Rekening Giro & Tabungan Mandiri (Auto-detect: Kopra/e-Statement/Rekening Koran)',
        'formats':     ['Kopra by Mandiri', 'e-Statement', 'Laporan Rekening Koran'],
        'enabled':     True,   # ✓ Aktif — parser Kopra sudah divalidasi checksum
    },
    'bni': {
        'name':        'Bank Negara Indonesia (BNI)',
        'short_name':  'BNI',
        'extractor':   BNIExtractor,
        'color':       '#F47920',   # oranye BNI
        'logo_text':   'BNI',
        'description': 'Rekening Giro BNI (Auto-detect: Account Statement/Transaction Inquiry)',
        'formats':     ['ACCOUNT STATEMENT', 'TRANSACTION INQUIRY'],
        'enabled':     True,   # ✓ Aktif — parser Account Statement & Transaction Inquiry sudah divalidasi checksum
    },
    'bri': {
        'name':        'Bank Rakyat Indonesia (BRI)',
        'short_name':  'BRI',
        'extractor':   BRIExtractor,
        'color':       '#00529C',   # biru BRI
        'logo_text':   'BRI',
        'description': 'Rekening Giro, BritAma & Simpedes BRI (Laporan Transaksi Finansial)',
        'formats':     ['Laporan Transaksi Finansial'],
        'enabled':     True,   # ✓ Aktif — parser Laporan Transaksi Finansial sudah divalidasi checksum
    },
    'mestika': {
        'name':        'Bank Mestika Dharma',
        'short_name':  'MESTIKA',
        'extractor':   MestikaExtractor,
        'color':       '#E30613',   # merah Mestika
        'logo_text':   'MDH',
        'description': 'Rekening Giro & Giro PRK Bank Mestika (Rekening Koran / Account Statement)',
        'formats':     ['Rekening Koran / Account Statement'],
        'enabled':     True,   # ✓ Aktif — parser Rekening Koran sudah divalidasi checksum
    },
}


def get_enabled_banks() -> dict:
    """Kembalikan hanya bank yang enabled=True."""
    return {k: v for k, v in BANK_REGISTRY.items() if v.get('enabled', False)}


def get_formats() -> list:
    """Seluruh format yang punya extractor, dari bank yang aktif."""
    return [
        (info['short_name'], fmt)
        for info in get_enabled_banks().values()
        for fmt in info.get('formats', [])
    ]


def get_extractor(bank_code: str):
    """
    Ambil kelas extractor berdasarkan kode bank.
    Raise ValueError jika bank tidak ditemukan atau belum aktif.
    """
    bank = BANK_REGISTRY.get(bank_code)
    if not bank:
        raise ValueError(f"Bank '{bank_code}' tidak ditemukan di registry.")
    if not bank.get('enabled', False):
        raise ValueError(f"Bank '{bank_code}' belum tersedia.")
    return bank['extractor']
