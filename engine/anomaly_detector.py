"""
anomaly_detector.py — Deteksi indikasi kejanggalan pada rekening koran BCA.

Modul ini menghasilkan daftar temuan (findings) yang dirender oleh
excel_builder.py sebagai Sheet "Indikasi Kejanggalan". Berbeda dari
engine/excel_builder.py dan engine/categorizer.py yang bank-agnostik,
sebagian pemeriksaan di sini (running balance per baris, nomor halaman,
template halaman, format nominal) membaca ULANG teks mentah PDF dengan
pola BCA — karena kontrak BaseExtractor (extract_saldo/extract_transaksi)
tidak menyimpan detail level-baris/halaman yang dibutuhkan.

Setiap finding adalah dict dengan struktur seragam:
    {
        'kategori':  str,   # nama indikasi, mis. 'Saldo Tidak Balance'
        'tingkat':   str,   # 'Tinggi' | 'Sedang' | 'Rendah'
        'bulan':     str,   # nama bulan atau '-'
        'tanggal':   str,   # tanggal/rentang atau '-'
        'halaman':   str,   # nomor halaman PDF (1-based) atau '-'
        'deskripsi': str,   # ringkasan temuan
        'detail':    str,   # bukti/angka pendukung
        'nilai_rp':  int|None,  # nominal terkait (jika relevan)
    }

Untuk menambah indikasi baru: tulis fungsi `_check_xxx(...)` yang
mengembalikan list finding, lalu daftarkan di `detect_anomalies()`.
"""

import re
import datetime

import pdfplumber

BULAN_ORDER = [
    'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
    'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'
]
BULAN_TO_NUM = {b: i + 1 for i, b in enumerate(BULAN_ORDER)}

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

TOLERANSI_SALDO = 100          # toleransi pembulatan int() saldo (Rp)
TOLERANSI_RUNNING_BALANCE = 5  # toleransi pembulatan running balance (Rp)

# PPh Final atas bunga tabungan/giro yang berlaku umum = 20%. Nilai bukan
# bulat karena hasil bagi int() dua nominal yang sudah dibulatkan duluan
# (Pajak Bunga dan Bunga masing-masing sudah dibulatkan rupiah penuh),
# jadi rasio sebenarnya jarang persis 0.2 — beri toleransi tipis.
RASIO_PAJAK_BUNGA_MIN = 0.195
RASIO_PAJAK_BUNGA_MAX = 0.205


def detect_anomalies(pdf_path: str, saldo_per_bulan: dict,
                      transaksi_per_bulan: dict) -> list:
    """Jalankan semua pemeriksaan dan kembalikan list finding (belum diurutkan)."""
    findings = []

    findings += _check_saldo_balance(saldo_per_bulan, transaksi_per_bulan)
    findings += _check_duplikasi(transaksi_per_bulan)
    findings += _check_gap_transaksi(transaksi_per_bulan)
    findings += _check_setoran_tunai_libur(transaksi_per_bulan, saldo_per_bulan)
    findings += _check_round_number_bias(transaksi_per_bulan)
    findings += _check_structuring(transaksi_per_bulan)
    findings += _check_rasio_pajak_bunga(transaksi_per_bulan)

    # Pemeriksaan yang butuh baca ulang PDF mentah (running balance, nomor
    # halaman, template, format angka, metadata). Kalau PDF tak terbaca
    # (mis. path tidak valid), lewati saja tanpa menggagalkan seluruh laporan.
    try:
        raw = _scan_pdf_raw(pdf_path)
        findings += _check_running_balance(raw)
        findings += _check_halaman_sequence(raw)
        findings += _check_template_halaman(raw)
        findings += _check_format_nominal(raw)
        findings += _check_mutasi_hilang(raw, transaksi_per_bulan)
        findings += _check_metadata_pdf(pdf_path)
    except Exception as e:
        findings.append({
            'kategori': 'Kesalahan Pemeriksaan',
            'tingkat': 'Rendah',
            'bulan': '-', 'tanggal': '-', 'halaman': '-',
            'deskripsi': 'Sebagian pemeriksaan berbasis PDF mentah gagal dijalankan',
            'detail': f'{type(e).__name__}: {e}',
            'nilai_rp': None,
        })

    tingkat_order = {'Tinggi': 0, 'Sedang': 1, 'Rendah': 2}
    findings.sort(key=lambda f: (
        tingkat_order.get(f['tingkat'], 9),
        BULAN_TO_NUM.get(f['bulan'], 99),
        str(f['tanggal']),
    ))
    return findings


# ============================================================
# CHECK 1 — Saldo tidak balance
# ============================================================

def _check_saldo_balance(saldo_per_bulan, transaksi_per_bulan):
    out = []
    for bulan, info in saldo_per_bulan.items():
        if bulan.startswith('_'):
            continue
        saldo_awal = saldo_per_bulan.get(f'_saldo_awal_{bulan}')
        df_s = info.get('df')
        if saldo_awal is None or df_s is None or df_s.empty:
            continue
        saldo_akhir = int(df_s['Saldo Akhir Harian'].iloc[-1])

        df_t = transaksi_per_bulan.get(bulan)
        kredit = int(df_t[df_t['Jenis Mutasi'] == 'Kredit']['Mutasi'].sum()) if df_t is not None else 0
        debit = int(df_t[df_t['Jenis Mutasi'] == 'Debit']['Mutasi'].sum()) if df_t is not None else 0

        expected = saldo_awal + kredit - debit
        selisih = expected - saldo_akhir
        if abs(selisih) > TOLERANSI_SALDO:
            out.append({
                'kategori': 'Saldo Tidak Balance',
                'tingkat': 'Tinggi',
                'bulan': bulan, 'tanggal': '-', 'halaman': '-',
                'deskripsi': f'Saldo Awal + Kredit − Debit tidak sama dengan Saldo Akhir di bulan {bulan}',
                'detail': (
                    f'Saldo Awal {saldo_awal:,} + Kredit {kredit:,} − Debit {debit:,} '
                    f'= {expected:,}, tapi Saldo Akhir tercatat {saldo_akhir:,}'
                ),
                'nilai_rp': selisih,
            })
    return out


# ============================================================
# CHECK 5 — Duplikasi transaksi
# ============================================================

def _check_duplikasi(transaksi_per_bulan):
    out = []
    for bulan, df in transaksi_per_bulan.items():
        if df is None or df.empty:
            continue
        subset = ['Tanggal', 'Jenis Mutasi', 'Mutasi', 'Keterangan Transaksi']
        dup_mask = df.duplicated(subset=subset, keep=False)
        if not dup_mask.any():
            continue
        grouped = df[dup_mask].groupby(subset).size().reset_index(name='jumlah')
        for _, row in grouped.iterrows():
            tingkat = 'Tinggi' if row['jumlah'] > 3 else 'Sedang'
            out.append({
                'kategori': 'Duplikasi Transaksi',
                'tingkat': tingkat,
                'bulan': bulan, 'tanggal': int(row['Tanggal']), 'halaman': '-',
                'deskripsi': f"Transaksi identik berulang {row['jumlah']}x pada tanggal {row['Tanggal']}",
                'detail': f"{row['Jenis Mutasi']} Rp{row['Mutasi']:,} — \"{row['Keterangan Transaksi']}\"",
                'nilai_rp': int(row['Mutasi']) * int(row['jumlah']),
            })
    return out


# ============================================================
# CHECK 3a — Gap tanggal tidak wajar (tanpa transaksi berturut-turut)
# ============================================================

def _check_gap_transaksi(transaksi_per_bulan, min_streak=5, min_total_txn=30):
    out = []
    for bulan, df in transaksi_per_bulan.items():
        if df is None or df.empty or len(df) < min_total_txn:
            continue
        tanggal_ada = sorted(df['Tanggal'].unique())
        if len(tanggal_ada) < 2:
            continue
        first_day, last_day = tanggal_ada[0], tanggal_ada[-1]
        semua_hari = set(range(first_day, last_day + 1))
        hari_kosong = sorted(semua_hari - set(tanggal_ada))

        streak, start = [], None
        for d in hari_kosong + [None]:
            if d is not None and (start is None or d == streak[-1] + 1):
                streak.append(d)
                start = start or d
            else:
                if len(streak) >= min_streak:
                    out.append({
                        'kategori': 'Mutasi Hilang / Gap Tidak Wajar',
                        'tingkat': 'Sedang',
                        'bulan': bulan, 'tanggal': f'{streak[0]}-{streak[-1]}', 'halaman': '-',
                        'deskripsi': f'Tidak ada transaksi selama {len(streak)} hari berturut-turut',
                        'detail': (
                            f'Rekening aktif ({len(df)} transaksi di bulan {bulan}) tapi kosong '
                            f'tanggal {streak[0]} s.d. {streak[-1]}'
                        ),
                        'nilai_rp': None,
                    })
                streak = [d] if d is not None else []
                start = d
    return out


# ============================================================
# CHECK 9 — Setoran Tunai di hari Minggu/hari libur
# ============================================================

def _check_setoran_tunai_libur(transaksi_per_bulan, saldo_per_bulan):
    out = []
    for bulan, df in transaksi_per_bulan.items():
        if df is None or df.empty:
            continue
        tahun = saldo_per_bulan.get(bulan, {}).get('tahun')
        bulan_num = BULAN_TO_NUM.get(bulan)
        if not tahun or not bulan_num:
            continue

        mask = df['Keterangan Transaksi'].str.upper().str.contains('SETORAN TUNAI', na=False)
        for _, row in df[mask].iterrows():
            tanggal = int(row['Tanggal'])
            try:
                d = datetime.date(int(tahun), bulan_num, tanggal)
            except ValueError:
                continue
            is_minggu = d.weekday() == 6
            is_libur_tetap = (bulan_num, tanggal) in LIBUR_TANGGAL_TETAP
            if is_minggu or is_libur_tetap:
                alasan = 'hari Minggu' if is_minggu else 'tanggal merah (libur nasional)'
                out.append({
                    'kategori': 'Setoran Tunai di Hari Libur',
                    'tingkat': 'Tinggi',
                    'bulan': bulan, 'tanggal': tanggal, 'halaman': '-',
                    'deskripsi': f'Setoran tunai tercatat pada {alasan} ({d.strftime("%d/%m/%Y")})',
                    'detail': f"Rp{int(row['Mutasi']):,} — \"{row['Keterangan Transaksi']}\"",
                    'nilai_rp': int(row['Mutasi']),
                })
    return out


# ============================================================
# CHECK 10a — Nominal bulat besar berulang (round-number bias)
# ============================================================

def _check_round_number_bias(transaksi_per_bulan, ambang=50_000_000, min_count=5):
    out = []
    for bulan, df in transaksi_per_bulan.items():
        if df is None or df.empty:
            continue
        bulat = df[(df['Mutasi'] >= ambang) & (df['Mutasi'] % 10_000_000 == 0)]
        if len(bulat) >= min_count:
            proporsi = len(bulat) / len(df) * 100
            out.append({
                'kategori': 'Nominal Bulat Berulang',
                'tingkat': 'Rendah',
                'bulan': bulan, 'tanggal': '-', 'halaman': '-',
                'deskripsi': f'{len(bulat)} transaksi bernilai sangat bulat (kelipatan Rp10 juta, ≥Rp{ambang:,})',
                'detail': f'{proporsi:.1f}% dari total {len(df)} transaksi bulan {bulan} — pola umum di transaksi rekayasa',
                'nilai_rp': int(bulat['Mutasi'].sum()),
            })
    return out


# ============================================================
# CHECK 10b — Indikasi structuring (transaksi tunai mendekati ambang LTKT Rp500jt)
# ============================================================

def _check_structuring(transaksi_per_bulan, ambang_bawah=400_000_000, ambang_atas=500_000_000):
    out = []
    for bulan, df in transaksi_per_bulan.items():
        if df is None or df.empty:
            continue
        mask_tunai = df['Keterangan Transaksi'].str.upper().str.contains('TUNAI', na=False)
        kandidat = df[mask_tunai & (df['Mutasi'] >= ambang_bawah) & (df['Mutasi'] < ambang_atas)]
        for tanggal, grp in kandidat.groupby('Tanggal'):
            out.append({
                'kategori': 'Indikasi Structuring',
                'tingkat': 'Sedang',
                'bulan': bulan, 'tanggal': int(tanggal), 'halaman': '-',
                'deskripsi': f'Transaksi tunai mendekati ambang pelaporan Rp{ambang_atas:,} (LTKT) pada tanggal {tanggal}',
                'detail': f"{len(grp)} transaksi, total Rp{int(grp['Mutasi'].sum()):,} — perlu verifikasi bukan upaya menghindari pelaporan",
                'nilai_rp': int(grp['Mutasi'].sum()),
            })
    return out


# ============================================================
# CHECK 10c — Rasio Pajak Bunga terhadap Bunga tidak wajar (≈20%)
# ============================================================

def _check_rasio_pajak_bunga(transaksi_per_bulan):
    """
    Bank umumnya memotong PPh Final 20% atas bunga tabungan/giro, jadi
    Pajak Bunga / Bunga seharusnya ≈0.20. Hanya cocokkan baris yang
    Keterangan Transaksi-nya PERSIS "BUNGA" / "PAJAK BUNGA" (bukan
    sekadar mengandung kata "bunga") — extractor BCA juga memberi label
    Nama "Bunga" untuk transaksi tak terkait seperti "KARANGAN BUNGA"
    (papan bunga dukacita), yang tidak boleh ikut ke perhitungan ini.
    """
    out = []
    for bulan, df in transaksi_per_bulan.items():
        if df is None or df.empty:
            continue
        ket = df['Keterangan Transaksi'].astype(str).str.strip().str.upper()

        bunga_rows = df[(df['Jenis Mutasi'] == 'Kredit') & (ket == 'BUNGA')]
        pajak_rows = df[(df['Jenis Mutasi'] == 'Debit') & (ket == 'PAJAK BUNGA')]

        tanggal_terkait = sorted(set(bunga_rows['Tanggal']) | set(pajak_rows['Tanggal']))
        for tanggal in tanggal_terkait:
            bunga_amt = int(bunga_rows[bunga_rows['Tanggal'] == tanggal]['Mutasi'].sum())
            pajak_amt = int(pajak_rows[pajak_rows['Tanggal'] == tanggal]['Mutasi'].sum())

            if bunga_amt == 0 or pajak_amt == 0:
                # Salah satu tidak ditemukan — bisa jadi wajar (mis. bunga di
                # bawah ambang bebas pajak), tapi tetap layak dicatat sebagai info.
                out.append({
                    'kategori': 'Rasio Pajak Bunga Tidak Wajar',
                    'tingkat': 'Rendah',
                    'bulan': bulan, 'tanggal': int(tanggal), 'halaman': '-',
                    'deskripsi': 'Bunga tercatat tanpa pasangan Pajak Bunga (atau sebaliknya)',
                    'detail': f'Bunga: Rp{bunga_amt:,} | Pajak Bunga: Rp{pajak_amt:,} — cek apakah salah satunya hilang saat ekstraksi',
                    'nilai_rp': None,
                })
                continue

            rasio = pajak_amt / bunga_amt
            if not (RASIO_PAJAK_BUNGA_MIN <= rasio <= RASIO_PAJAK_BUNGA_MAX):
                out.append({
                    'kategori': 'Rasio Pajak Bunga Tidak Wajar',
                    'tingkat': 'Sedang',
                    'bulan': bulan, 'tanggal': int(tanggal), 'halaman': '-',
                    'deskripsi': f'Rasio Pajak Bunga/Bunga {rasio:.4f} di luar rentang wajar ({RASIO_PAJAK_BUNGA_MIN}-{RASIO_PAJAK_BUNGA_MAX}, PPh Final 20%)',
                    'detail': f'Bunga: Rp{bunga_amt:,} | Pajak Bunga: Rp{pajak_amt:,} | Rasio: {rasio:.4f}',
                    'nilai_rp': pajak_amt,
                })
    return out


# ============================================================
# RAW PDF SCAN — dipakai bersama oleh beberapa check di bawah
# ============================================================

def _scan_pdf_raw(pdf_path: str) -> dict:
    """
    Baca ulang PDF secara mentah untuk hal-hal yang tidak disimpan kontrak
    BaseExtractor: nomor halaman, header/template tiap halaman, baris
    running balance, dan pola format angka yang tidak standar.
    """
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for idx, page in enumerate(pdf.pages):
            text = page.extract_text() or ''
            lines = text.split('\n')

            halaman_match = re.search(r'HALAMAN\s*:\s*(\d+)\s*/\s*(\d+)', text)
            periode_match = re.search(r'PERIODE\s*:\s*(\w+)\s+(\d{4})', text)
            has_kolom_header = any('TANGGAL' in l and 'KETERANGAN' in l and 'SALDO' in l for l in lines)

            tx_lines = []
            for line in lines:
                m = re.match(r'^(\d{2})/(\d{2})\s+(.+)', line)
                if not m:
                    continue
                rest = m.group(3)
                if 'SALDO AWAL' in rest:
                    continue
                # Nominal standar: grup ribuan koma + 2 desimal.
                nominal_matches = re.findall(
                    r'(?<![\d.])\d{1,3}(?:,\d{3})*\.\d{2}(?!\d)', rest
                )
                # Fragmen format Eropa (titik ribuan, koma desimal) yang nyasar
                # ke baris transaksi — indikasi kualitas sumber/OCR bermasalah.
                format_asing = re.findall(r'\d{1,3}(?:\.\d{3}){2,}(?:,\d{2})?', rest)

                # Aturan klasifikasi ini WAJIB sinkron dengan BCAExtractor._parse_transaction
                # di extractors/bca.py — termasuk fallback BIAYA/TARIKAN/PAJAK yang tidak
                # punya penanda CR/DB eksplisit. Dicek atas SATU BARIS PENUH (bukan cuma
                # teks sebelum nominal): beberapa tipe transaksi menaruh "DB" SETELAH
                # nominal, mis. "TRANSAKSI DEBIT TGL: 01/06 34,000.00 DB" — kalau cuma
                # baca sebelum nominal, penanda "DB" di ujung baris itu terlewat dan
                # transaksi debit malah kehitung kredit (running balance meleset 2x lipat
                # nominalnya).
                line_upper = rest.upper()
                if 'KR OTOMATIS' in line_upper or re.match(r'^KR\b', line_upper):
                    jenis = 'Kredit'
                elif re.search(r'\bCR\b', line_upper):
                    jenis = 'Kredit'
                elif re.search(r'\bDB\b', line_upper):
                    jenis = 'Debit'
                elif 'TARIKAN' in line_upper or 'PAJAK' in line_upper or re.search(
                    r'BIAYA (ADM|TRANSFER|ADMINISTRASI)|BIAYA TXN', line_upper
                ):
                    jenis = 'Debit'
                else:
                    jenis = 'Kredit'

                tx_lines.append({
                    'tanggal': int(m.group(1)),
                    'nominal_matches': nominal_matches,
                    'jenis': jenis,
                    'format_asing': format_asing,
                    'raw': line,
                })

            pages.append({
                'index': idx,
                'no_halaman': int(halaman_match.group(1)) if halaman_match else None,
                'total_halaman': int(halaman_match.group(2)) if halaman_match else None,
                'periode': f'{periode_match.group(1).capitalize()} {periode_match.group(2)}' if periode_match else None,
                'has_kolom_header': has_kolom_header,
                'tx_lines': tx_lines,
                'saldo_awal_match': re.search(r'SALDO AWAL\s*:?\s*(-?[\d,]+\.\d{2})', text),
                'mutasi_cr_match': re.search(r'MUTASI CR\s*:\s*(-?[\d,]+\.\d{2})\s+(\d+)', text),
                'mutasi_db_match': re.search(r'MUTASI DB\s*:\s*(-?[\d,]+\.\d{2})\s+(\d+)', text),
            })
    return {'pages': pages}


# ============================================================
# CHECK 2 — Running balance tidak konsisten
# ============================================================

def _check_running_balance(raw):
    out = []
    running = None
    current_periode = object()  # sentinel unik, beda dari periode manapun (termasuk None)
    for page in raw['pages']:
        # Reset saldo berjalan tiap kali masuk periode (bulan) baru — beberapa PDF
        # BCA menyusun halaman antar-bulan TIDAK kronologis (mis. Juni, lalu Mei,
        # lalu Juli), jadi saldo akhir bulan sebelumnya tidak boleh "nyambung"
        # begitu saja ke saldo awal bulan berikutnya.
        if page['periode'] != current_periode:
            current_periode = page['periode']
            running = None
        if page['saldo_awal_match'] and running is None:
            running = float(page['saldo_awal_match'].group(1).replace(',', ''))
        for tx in page['tx_lines']:
            if not tx['nominal_matches']:
                continue
            try:
                amt = float(tx['nominal_matches'][0].replace(',', ''))
            except ValueError:
                continue
            if running is None:
                continue
            delta = amt if tx['jenis'] == 'Kredit' else -amt
            expected = running + delta

            if len(tx['nominal_matches']) > 1:
                try:
                    reported = float(tx['nominal_matches'][1].replace(',', ''))
                except ValueError:
                    reported = None
                if reported is not None and abs(reported - expected) > TOLERANSI_RUNNING_BALANCE:
                    out.append({
                        'kategori': 'Running Balance Tidak Konsisten',
                        'tingkat': 'Tinggi',
                        'bulan': page['periode'] or '-', 'tanggal': tx['tanggal'],
                        'halaman': page['index'] + 1,
                        'deskripsi': 'Saldo berjalan tidak sesuai dengan mutasi tercatat',
                        'detail': (
                            f'Perkiraan saldo {expected:,.2f} vs tercetak {reported:,.2f} '
                            f'(selisih {reported - expected:,.2f}) — baris: "{tx["raw"]}"'
                        ),
                        'nilai_rp': int(reported - expected),
                    })
                    running = reported  # resync ke angka tercetak, lanjut dari sana
                    continue
            running = expected
    return out


# ============================================================
# CHECK 4 — Nomor halaman/periode tidak berurutan
# ============================================================

def _check_halaman_sequence(raw):
    out = []
    pages = [p for p in raw['pages'] if p['no_halaman'] is not None]
    prev = None
    for p in pages:
        if prev and p['periode'] == prev['periode']:
            if p['no_halaman'] != prev['no_halaman'] + 1:
                out.append({
                    'kategori': 'Halaman/Periode Tidak Berurutan',
                    'tingkat': 'Tinggi',
                    'bulan': p['periode'] or '-', 'tanggal': '-', 'halaman': p['index'] + 1,
                    'deskripsi': f"Nomor halaman meloncat dari {prev['no_halaman']} ke {p['no_halaman']}",
                    'detail': f'Kemungkinan ada halaman yang hilang atau disisipkan (posisi file: halaman ke-{p["index"]+1})',
                    'nilai_rp': None,
                })
            if p['total_halaman'] != prev['total_halaman']:
                out.append({
                    'kategori': 'Halaman/Periode Tidak Berurutan',
                    'tingkat': 'Sedang',
                    'bulan': p['periode'] or '-', 'tanggal': '-', 'halaman': p['index'] + 1,
                    'deskripsi': f"Total halaman berubah dari {prev['total_halaman']} jadi {p['total_halaman']} dalam periode yang sama",
                    'detail': f'Posisi file: halaman ke-{p["index"]+1}',
                    'nilai_rp': None,
                })
        prev = p
    return out


# ============================================================
# CHECK 7 — Halaman berbeda template
# ============================================================

def _check_template_halaman(raw):
    out = []
    for p in raw['pages']:
        if p['tx_lines'] and not p['has_kolom_header']:
            out.append({
                'kategori': 'Template Halaman Berbeda',
                'tingkat': 'Sedang',
                'bulan': p['periode'] or '-', 'tanggal': '-', 'halaman': p['index'] + 1,
                'deskripsi': 'Halaman berisi transaksi tapi header kolom standar (TANGGAL/KETERANGAN/SALDO) tidak ditemukan',
                'detail': 'Bisa jadi halaman disisipkan dari sumber lain atau layout diedit',
                'nilai_rp': None,
            })
    return out


# ============================================================
# CHECK 6 — Format nominal/tanggal tidak konsisten
# ============================================================

def _check_format_nominal(raw):
    out = []
    for p in raw['pages']:
        for tx in p['tx_lines']:
            if tx['format_asing']:
                out.append({
                    'kategori': 'Format Nominal Tidak Konsisten',
                    'tingkat': 'Sedang',
                    'bulan': p['periode'] or '-', 'tanggal': tx['tanggal'], 'halaman': p['index'] + 1,
                    'deskripsi': 'Ditemukan format angka non-standar (titik ribuan/koma desimal) di baris transaksi',
                    'detail': f'Baris: "{tx["raw"]}" — kemungkinan hasil edit/OCR, nominal wajib dicek manual ke PDF asli',
                    'nilai_rp': None,
                })
    return out


# ============================================================
# CHECK 3b — Mutasi hilang (jumlah transaksi vs klaim PDF)
# ============================================================

def _check_mutasi_hilang(raw, transaksi_per_bulan):
    out = []
    declared = {}
    for p in raw['pages']:
        if p['mutasi_cr_match'] and p['periode']:
            bulan = p['periode'].split(' ')[0]
            declared.setdefault(bulan, {})['cr_n'] = int(p['mutasi_cr_match'].group(2))
        if p['mutasi_db_match'] and p['periode']:
            bulan = p['periode'].split(' ')[0]
            declared.setdefault(bulan, {})['db_n'] = int(p['mutasi_db_match'].group(2))

    for bulan, d in declared.items():
        df = transaksi_per_bulan.get(bulan)
        n_kredit = int((df['Jenis Mutasi'] == 'Kredit').sum()) if df is not None else 0
        n_debit = int((df['Jenis Mutasi'] == 'Debit').sum()) if df is not None else 0

        if 'cr_n' in d and d['cr_n'] != n_kredit:
            out.append({
                'kategori': 'Mutasi Hilang / Gap Tidak Wajar',
                'tingkat': 'Tinggi',
                'bulan': bulan, 'tanggal': '-', 'halaman': '-',
                'deskripsi': f'Jumlah transaksi Kredit hasil ekstraksi tidak sama dengan klaim PDF di bulan {bulan}',
                'detail': f"PDF mengklaim {d['cr_n']} transaksi Kredit, hasil ekstraksi {n_kredit}",
                'nilai_rp': None,
            })
        if 'db_n' in d and d['db_n'] != n_debit:
            out.append({
                'kategori': 'Mutasi Hilang / Gap Tidak Wajar',
                'tingkat': 'Tinggi',
                'bulan': bulan, 'tanggal': '-', 'halaman': '-',
                'deskripsi': f'Jumlah transaksi Debit hasil ekstraksi tidak sama dengan klaim PDF di bulan {bulan}',
                'detail': f"PDF mengklaim {d['db_n']} transaksi Debit, hasil ekstraksi {n_debit}",
                'nilai_rp': None,
            })
    return out


# ============================================================
# CHECK 8 — Metadata PDF mencurigakan
# ============================================================

SOFTWARE_EDITOR_MENCURIGAKAN = [
    'PHOTOSHOP', 'ILLUSTRATOR', 'CANVA', 'SMALLPDF', 'ILOVEPDF', 'PDF24',
    'PDFESCAPE', 'SEJDA', 'PDF-XCHANGE EDITOR', 'FOXIT PHANTOMPDF',
    'MICROSOFT WORD', 'GOOGLE DOCS',
]


def _check_metadata_pdf(pdf_path):
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        meta = pdf.metadata or {}

    producer = str(meta.get('Producer', '') or '')
    creator = str(meta.get('Creator', '') or '')
    created = meta.get('CreationDate')
    modified = meta.get('ModDate')

    # Selalu tampilkan metadata mentah sebagai baris info, terlepas dari
    # mencurigakan atau tidak — transparan untuk direview manual.
    out.append({
        'kategori': 'Metadata PDF',
        'tingkat': 'Rendah',
        'bulan': '-', 'tanggal': '-', 'halaman': '-',
        'deskripsi': 'Info metadata PDF (untuk review manual)',
        'detail': f'Producer: {producer or "-"} | Creator: {creator or "-"} | Created: {created or "-"} | Modified: {modified or "-"}',
        'nilai_rp': None,
    })

    gabungan = f'{producer} {creator}'.upper()
    for kw in SOFTWARE_EDITOR_MENCURIGAKAN:
        if kw in gabungan:
            out.append({
                'kategori': 'Metadata PDF Mencurigakan',
                'tingkat': 'Sedang',
                'bulan': '-', 'tanggal': '-', 'halaman': '-',
                'deskripsi': f'PDF tercatat dibuat/diedit dengan software "{kw.title()}", bukan software cetak rekening umum',
                'detail': f'Producer: {producer or "-"} | Creator: {creator or "-"} — perlu verifikasi keaslian dokumen',
                'nilai_rp': None,
            })
            break

    if created and modified and created != modified:
        out.append({
            'kategori': 'Metadata PDF Mencurigakan',
            'tingkat': 'Sedang',
            'bulan': '-', 'tanggal': '-', 'halaman': '-',
            'deskripsi': 'Tanggal modifikasi PDF berbeda dari tanggal pembuatan',
            'detail': f'Created: {created} | Modified: {modified} — indikasi file pernah diedit setelah dibuat',
            'nilai_rp': None,
        })

    return out
