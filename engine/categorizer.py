"""
categorizer.py — Kategorisasi transaksi kredit & debit.

Modul ini sepenuhnya bank-agnostik: tidak mengetahui dari bank mana
data berasal. Ia hanya membaca kolom standar dari Sheet 2 (Detail Transaksi):
  - Keterangan Transaksi
  - Nama Pengirim/Penerima
  - Mutasi

Keyword dapat dikustomisasi atau diperluas tanpa menyentuh logika engine.
"""

# ===== KEYWORD KATEGORI DEBIT =====
KATEGORI_DEBIT_KEYWORDS = {
    'Pembayaran Gaji': [
        'GAJI', 'PAYROLL', 'SALARY', 'UPAH', 'THR', ' THR ', 'BONUS', 'INSENTIF', 'KASBON'
    ],
    'Operasional Kantor': [
        ' PLN ', 'LISTRIK', 'ELECTRICITY', ' PDAM ', ' AIR ', 'WATER', 'TELKOM', 'TELEPON',
        ' WIFI ', 'INDIHOME', 'PASCABAYAR', 'TELKOMSEL', 'INDOSAT', 'PULSA',
        ' ATK ', 'ALAT TULIS', 'STATIONERY', 'PERLENGKAPAN KANTOR', 'MATERAI',
        'CLEANING', 'KEBERSIHAN', 'SECURITY', 'KEAMANAN', 'SATPAM', 'MAINTENANCE KANTOR',
        ' BPJS ', 'JAMSOSTEK', 'KESEHATAN', 'KETENAGAKERJAAN', 'NOTARIS', 'LEGALITAS',
        'IZIN USAHA', 'SEWA KANTOR', 'SEWA GEDUNG', 'RENT OFFICE', 'SEWA GUDANG',
        'TRAVELOKA', 'AGODA', 'TOKOPEDIA', 'SHOPEE'
    ],
    'Operasional Kendaraan': [
        'BBM', 'SOLAR', 'BENSIN', 'PERTAMAX', 'PERTALITE', 'PERTAMINA', 'SHELL', 'VIVO', 'SPBU',
        'SERVIS', 'SERVICE', 'SPARE PART', 'SPAREPART', ' BAN ', ' OLI ', 'ACCU', 'KAMPAS REM', 'BENGKEL',
        ' TOL ', ' TOLL ', 'ETOLL', 'E-TOLL', 'PARKIR', 'PARKING', 'UANG JALAN',
        'ONGKIR', 'ONGKOS', 'DP ONGKIR', 'PELUNASAN ONGKIR',
        'PAJAK KENDARAAN', ' STNK ', ' KIR ', ' BPKB ', 'SAMSAT'
    ],
    'Angsuran Kredit': [
        'ANGSURAN', 'CICILAN', 'INSTALLMENT', 'PEMBAYARAN PINJAMAN', 'PEMBAYARAN KREDIT',
        'PELUNASAN KREDIT', 'LEASING', 'BCA FINANCE', ' BCAF ', 'ADIRA', 'MANDIRI TUNAS FINANCE',
        ' MTF ', 'MEGA FINANCE', 'BFI FINANCE', 'CLIPAN FINANCE', 'HINO FINANCE',
        'INDOMOBIL FINANCE', 'OTO MULTIARTHA', 'TOYOTA ASTRA FINAN', 'SUMMIT OTO FINANCE',
        'MANDIRI UTAMA FINANCE', 'MITSUI LEASING', ' CSUL ', 'AUTOCOL', 'OTOGRAB'
    ],
    'Pembayaran Vendor/Supplier': [
        'VENDOR', 'SUPPLIER', 'PEMBELIAN'
    ],
    'Biaya Bank': [
        'BIAYA TRANSFER', 'BIAYA TXN', 'BIAYA ADM', 'BIAYA ADMIN', 'ADMIN FEE',
        'BIAYA ADMINISTRASI', 'PAJAK'
    ],
    'Transfer Internal (pindahbuku)': [
        'PINDAH BUKU', 'PINDAHBUKU', 'OVERBOOKING', 'TRANSFER INTERNAL', 'INTERNAL TRANSFER', 'PINBUK'
    ]
}

# ===== KEYWORD KATEGORI KREDIT =====
KATEGORI_KREDIT_KEYWORDS = {
    'Setoran Tunai': [
        'SETORAN TUNAI', 'SETORAN', 'CDM', 'CASH DEPOSIT'
    ],
    'Bunga/Jasa Giro': {
        'keterangan': ['JASA GIRO', 'BUNGA GIRO', 'BUNGA KREDIT'],
        'nama':       ['BUNGA']
    },
    'Transfer Internal (pindahbuku)': [
        'PINDAH BUKU', 'PINDAHBUKU', 'OVERBOOKING', 'TRANSFER INTERNAL', 'INTERNAL TRANSFER', 'PINBUK'
    ],
    'Penerimaan Pinjaman': {
        'keterangan': ['PENCAIRAN', 'PINJAMAN', 'LOAN', 'PEMBIAYAAN', 'DISBURSEMENT'],
        'nama': [
            'BCA FINANCE', 'BCAF', 'ADIRA', 'MANDIRI TUNAS FINANCE', 'MTF', 'MEGA FINANCE',
            'BFI FINANCE', 'CLIPAN FINANCE', 'HINO FINANCE', 'INDOMOBIL FINANCE',
            'OTO MULTIARTHA', 'TOYOTA ASTRA FINAN', 'SUMMIT OTO FINANCE',
            'MANDIRI UTAMA FINANCE', 'MITSUI LEASING', 'CSUL'
        ]
    },
    'Refund/Pengembalian Dana': [
        'REFUND', 'RETUR', 'PENGEMBALIAN DANA', 'TOLAKAN KLIRING', 'REVERSAL'
    ],
    'Pelunasan Piutang': [
        'CICILAN HUTANG', 'ANGSURAN HUTANG', 'PELUNASAN TAGIHAN', 'PEMBAYARAN HUTANG',
        'BAYAR HUTANG', 'PEMBAYARAN TAGIHAN', 'BAYAR TAGIHAN'
    ],
    'Klaim Asuransi': [
        'KLAIM ASURANSI', 'CLAIM ASURANSI', 'KLAIM', 'CLAIM', 'ALLIANZ',
        'PRUDENTIAL', 'MANULIFE', 'INSURANCE', 'ASURANSI'
    ],
    'Pendapatan Usaha': {
        'keterangan':   ['INVOICE', ' INV ', 'TRUCKING', 'ONGKIR', 'ONGKOS', 'DP ONGKIR', 'PELUNASAN ONGKIR', 'Trucking'],
        'nama_pattern': ['PT ', ' PT', 'PT.', 'CV ', ' CV', 'CV.', ' UD ', 'UD ', 'UD.', 'FIRMA']
    }
}


def kategorisasi_debit(keterangan: str, nama_penerima: str,
                       mutasi: float, nama_perusahaan: str) -> str:
    """
    Kategorisasi transaksi debit berdasarkan keyword.

    Args:
        keterangan:      Keterangan transaksi
        nama_penerima:   Nama penerima
        mutasi:          Jumlah transaksi
        nama_perusahaan: Nama pemilik rekening (untuk deteksi transfer internal)

    Returns:
        Nama kategori (str)
    """
    ket_upper  = (keterangan    or '').upper()
    nama_upper = (nama_penerima or '').upper()

    # 1. Biaya Bank — cek nominal Rp 2.500 (paling spesifik)
    if mutasi == 2500:
        return 'Biaya Bank'

    # 2. Cek keyword per kategori (kecuali Vendor dan Transfer Internal)
    for kategori, keywords in KATEGORI_DEBIT_KEYWORDS.items():
        if kategori in ('Pembayaran Vendor/Supplier', 'Transfer Internal (pindahbuku)'):
            continue
        for keyword in keywords:
            if keyword in ket_upper:
                return kategori

    # 3. Transfer Internal — keyword + nama perusahaan
    if nama_perusahaan:
        words = nama_perusahaan.upper().split()[:2]
        perusahaan_key = ' '.join(words) if len(words) >= 2 else nama_perusahaan.upper()
        for keyword in KATEGORI_DEBIT_KEYWORDS['Transfer Internal (pindahbuku)']:
            if keyword in ket_upper:
                return 'Transfer Internal (pindahbuku)'
        if len(perusahaan_key) >= 5 and perusahaan_key in nama_upper:
            return 'Transfer Internal (pindahbuku)'

    # 4. Pembayaran Vendor/Supplier
    for keyword in KATEGORI_DEBIT_KEYWORDS['Pembayaran Vendor/Supplier']:
        if keyword in ket_upper:
            return 'Pembayaran Vendor/Supplier'
    if any(x in nama_upper for x in ['PT ', ' PT', 'PT.', 'CV ', ' CV', 'CV.', ' UD ', 'UD ', 'UD.', 'FIRMA']):
        return 'Pembayaran Vendor/Supplier'

    return 'Lain-lain Tanpa Keterangan'


def kategorisasi_kredit(keterangan: str, nama_pengirim: str,
                        mutasi: float, nama_perusahaan: str) -> str:
    """
    Kategorisasi transaksi kredit berdasarkan keyword.

    Args:
        keterangan:      Keterangan transaksi
        nama_pengirim:   Nama pengirim
        mutasi:          Jumlah transaksi
        nama_perusahaan: Nama pemilik rekening (untuk deteksi transfer internal)

    Returns:
        Nama kategori (str)
    """
    ket_upper  = (keterangan   or '').upper()
    nama_upper = (nama_pengirim or '').upper()

    # 1. Setoran Tunai
    for kw in KATEGORI_KREDIT_KEYWORDS['Setoran Tunai']:
        if kw in ket_upper:
            return 'Setoran Tunai'

    # 2. Bunga/Jasa Giro
    for kw in KATEGORI_KREDIT_KEYWORDS['Bunga/Jasa Giro']['keterangan']:
        if kw in ket_upper:
            return 'Bunga/Jasa Giro'
    for kw in KATEGORI_KREDIT_KEYWORDS['Bunga/Jasa Giro']['nama']:
        if kw in nama_upper:
            return 'Bunga/Jasa Giro'

    # 3. Transfer Internal
    for kw in KATEGORI_KREDIT_KEYWORDS['Transfer Internal (pindahbuku)']:
        if kw in ket_upper:
            return 'Transfer Internal (pindahbuku)'
    if nama_perusahaan:
        words = nama_perusahaan.upper().split()[:2]
        perusahaan_key = ' '.join(words) if len(words) >= 2 else nama_perusahaan.upper()
        if len(perusahaan_key) >= 5 and perusahaan_key in nama_upper:
            return 'Transfer Internal (pindahbuku)'

    # 4. Penerimaan Pinjaman
    for kw in KATEGORI_KREDIT_KEYWORDS['Penerimaan Pinjaman']['keterangan']:
        if kw in ket_upper:
            return 'Penerimaan Pinjaman'
    for kw in KATEGORI_KREDIT_KEYWORDS['Penerimaan Pinjaman']['nama']:
        if kw in nama_upper:
            return 'Penerimaan Pinjaman'

    # 5. Refund
    for kw in KATEGORI_KREDIT_KEYWORDS['Refund/Pengembalian Dana']:
        if kw in ket_upper:
            return 'Refund/Pengembalian Dana'

    # 6. Pelunasan Piutang
    for kw in KATEGORI_KREDIT_KEYWORDS['Pelunasan Piutang']:
        if kw in ket_upper:
            return 'Pelunasan Piutang'

    # 7. Klaim Asuransi
    for kw in KATEGORI_KREDIT_KEYWORDS['Klaim Asuransi']:
        if kw in ket_upper:
            return 'Klaim Asuransi'

    # 8. Pendapatan Usaha
    for kw in KATEGORI_KREDIT_KEYWORDS['Pendapatan Usaha']['keterangan']:
        if kw in ket_upper:
            return 'Pendapatan Usaha'
    for pattern in KATEGORI_KREDIT_KEYWORDS['Pendapatan Usaha']['nama_pattern']:
        if pattern in nama_upper:
            return 'Pendapatan Usaha'

    return 'Lain-lain'
