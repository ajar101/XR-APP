"""
registry.py — Daftar bank yang tersedia di aplikasi.

Untuk menambah bank baru:
  1. Buat file extractor baru di extractors/<nama_bank>.py
  2. Import kelasnya di sini
  3. Tambahkan entry di BANK_REGISTRY

Tidak perlu mengubah file lain (app.py, engine, UI) sama sekali.
"""

from extractors.bca import BCAExtractor
from extractors.mandiri import MandiriExtractor
from extractors.bni import BNIExtractor

BANK_REGISTRY = {
    'bca': {
        'name':        'Bank Central Asia (BCA)',
        'short_name':  'BCA',
        'extractor':   BCAExtractor,
        'color':       '#005BAA',   # biru BCA — untuk UI
        'logo_text':   'BCA',       # placeholder sebelum ada asset logo
        'description': 'Rekening Giro & Tabungan BCA',
        'enabled':     True,
    },
    'mandiri': {
        'name':        'Bank Mandiri',
        'short_name':  'MANDIRI',
        'extractor':   MandiriExtractor,
        'color':       '#003D7C',   # biru Mandiri
        'logo_text':   'MDR',       # logo placeholder
        'description': 'Rekening Giro & Tabungan Mandiri (Auto-detect: Kopra/E-Banking/Statement)',
        'enabled':     True,   # ✓ Aktif — parser Kopra sudah divalidasi checksum
    },
    'bni': {
        'name':        'Bank Negara Indonesia (BNI)',
        'short_name':  'BNI',
        'extractor':   BNIExtractor,
        'color':       '#F47920',   # oranye BNI
        'logo_text':   'BNI',
        'description': 'Rekening Giro BNI (e-Statement)',
        'enabled':     False,  # ⏸ Dinonaktifkan sementara — fokus stabilisasi BCA
    },
}


def get_enabled_banks() -> dict:
    """Kembalikan hanya bank yang enabled=True."""
    return {k: v for k, v in BANK_REGISTRY.items() if v.get('enabled', False)}


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
