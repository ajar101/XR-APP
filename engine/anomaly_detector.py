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

import os
import re
import datetime

import pdfplumber

from engine.categorizer import kategorisasi_debit, kategorisasi_kredit

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

# Angka bergaya Eropa (titik ribuan, koma desimal) di antara baris berformat
# standar: "15.840.000,B".
FORMAT_ASING_RE = re.compile(r'\d{1,3}(?:\.\d{3}){2,}(?:,\d{2})?')

TOLERANSI_SALDO = 100          # toleransi pembulatan int() saldo (Rp)
TOLERANSI_RUNNING_BALANCE = 5  # toleransi pembulatan running balance (Rp)

# PPh Final atas bunga tabungan/giro yang berlaku umum = 20%. Nilai bukan
# bulat karena hasil bagi int() dua nominal yang sudah dibulatkan duluan
# (Pajak Bunga dan Bunga masing-masing sudah dibulatkan rupiah penuh),
# jadi rasio sebenarnya jarang persis 0.2 — beri toleransi tipis.
RASIO_PAJAK_BUNGA_MIN = 0.195
RASIO_PAJAK_BUNGA_MAX = 0.205



def detect_anomalies(pdf_path, saldo_per_bulan: dict,
                      transaksi_per_bulan: dict, bank_name: str = None) -> list:
    """
    Jalankan semua pemeriksaan dan kembalikan list finding (belum diurutkan).

    `bank_name` hanya dipakai untuk pelabelan; tidak ada lagi pemeriksaan yang
    bergantung pada bank apa. Pemeriksaan yang dulu membaca ulang PDF dengan
    pola satu bank kini bekerja atas metadata '_provenance' dari extractor.

    `pdf_path` boleh satu path (str) atau list path — dipakai saat user
    upload beberapa PDF sekaligus (lihat engine/multi_pdf_merger.py).
    Pemeriksaan berbasis data gabungan (saldo/transaksi) jalan sekali atas
    seluruh bulan; pemeriksaan berbasis PDF mentah (running balance, nomor
    halaman, template, format angka, metadata) jalan PER FILE karena
    masing-masing PDF py punya nomor halaman & metadata sendiri-sendiri —
    kalau lebih dari satu file, nama filenya disertakan di kolom halaman
    supaya temuan bisa dilacak ke file yang mana.
    """
    findings = []

    findings += _check_saldo_balance(saldo_per_bulan, transaksi_per_bulan)
    findings += _check_duplikasi(transaksi_per_bulan, saldo_per_bulan)
    findings += _check_gap_transaksi(transaksi_per_bulan)
    findings += _check_setoran_tunai_libur(transaksi_per_bulan, saldo_per_bulan)
    findings += _check_rtgs_libur(transaksi_per_bulan, saldo_per_bulan)
    findings += _check_round_number_bias(transaksi_per_bulan)
    findings += _check_structuring(transaksi_per_bulan)
    findings += _check_rasio_pajak_bunga(transaksi_per_bulan, saldo_per_bulan)
    findings += _check_jadwal_biaya_adm(transaksi_per_bulan, saldo_per_bulan)
    findings += _check_checksum_extractor(saldo_per_bulan)
    findings += _check_peringatan_extractor(saldo_per_bulan)
    findings += _check_urutan_tanggal(transaksi_per_bulan)

    # Pemeriksaan atas jejak cetak dokumen. Datanya dari extractor
    # (metadata '_provenance'), jadi bank-agnostik: extractor yang tahu tata
    # letak dokumennya, engine hanya membaca faktanya.
    prov = _provenance(saldo_per_bulan)
    findings += _check_running_balance(prov, saldo_per_bulan)
    findings += _check_halaman_sequence(prov)
    findings += _check_template_halaman(prov)
    findings += _check_format_nominal(prov)

    pdf_paths = [pdf_path] if isinstance(pdf_path, str) else list(pdf_path)
    beri_label_file = len(pdf_paths) > 1

    # Satu-satunya pemeriksaan yang masih membuka PDF: metadata berkas
    # (Producer/Creator/tanggal). Isinya properti berkas, bukan tata letak,
    # jadi berlaku untuk bank apa pun. Kalau satu berkas tak terbaca, lewati
    # berkas itu saja — jangan gagalkan seluruh laporan.
    for path in pdf_paths:
        label = os.path.basename(path)
        try:
            file_findings = list(_check_metadata_pdf(path))
            if beri_label_file:
                for f in file_findings:
                    if f['halaman'] not in (None, '-'):
                        f['halaman'] = f'{f["halaman"]} ({label})'
                    else:
                        f['halaman'] = label
            findings += file_findings
        except Exception as e:
            findings.append({
                'kategori': 'Kesalahan Pemeriksaan',
                'tingkat': 'Rendah',
                'bulan': '-', 'tanggal': '-', 'halaman': label if beri_label_file else '-',
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

def _adalah_biaya_bank(row, nama_perusahaan: str) -> bool:
    """Apakah baris ini terkategori 'Biaya Bank' menurut categorizer bersama."""
    fungsi = (kategorisasi_debit if row['Jenis Mutasi'] == 'Debit'
              else kategorisasi_kredit)
    try:
        return fungsi(row['Keterangan Transaksi'], '',
                      row['Mutasi'], nama_perusahaan) == 'Biaya Bank'
    except Exception:
        # Kategorisasi gagal bukan alasan menghilangkan temuan — biarkan
        # baris ini tetap diperiksa seperti biasa.
        return False


def _check_duplikasi(transaksi_per_bulan, saldo_per_bulan=None):
    """
    Transaksi identik berulang di tanggal yang sama.

    Baris BIAYA BANK dikecualikan. Biaya per-transaksi (mis. biaya transfer
    BI Fast Rp2.500) MEMANG wajib berulang identik setiap kali ada transfer,
    jadi pengulangannya bukan sinyal apa pun — premis pemeriksaan ini tidak
    berlaku untuk baris seperti itu. Tanpa pengecualian ini, satu rekening
    yang aktif bertransfer bisa menghasilkan puluhan temuan yang seluruhnya
    benign dan menenggelamkan temuan yang sungguhan.

    Pengecualiannya bank-agnostik: memakai engine/categorizer.py, yang sudah
    jadi kosakata kategori bersama untuk semua bank.
    """
    out = []
    nama_perusahaan = (saldo_per_bulan or {}).get('_nama_pemilik', '') or ''
    for bulan, df in transaksi_per_bulan.items():
        if df is None or df.empty:
            continue
        subset = ['Tanggal', 'Jenis Mutasi', 'Mutasi', 'Keterangan Transaksi']
        dup_mask = df.duplicated(subset=subset, keep=False)
        if not dup_mask.any():
            continue
        grouped = df[dup_mask].groupby(subset).size().reset_index(name='jumlah')
        for _, row in grouped.iterrows():
            if _adalah_biaya_bank(row, nama_perusahaan):
                continue
            # Jumlah pengulangan TIDAK menaikkan tingkat indikasi. Transaksi
            # rutin memang wajar berulang identik di hari yang sama (setoran
            # per shift, pembayaran per unit, penarikan bertahap), jadi
            # menganggap >3 pengulangan sebagai indikasi tinggi menghasilkan
            # false positive pada rekening operasional yang sibuk.
            out.append({
                'kategori': 'Duplikasi Transaksi',
                'tingkat': 'Sedang',
                'bulan': bulan, 'tanggal': int(row['Tanggal']), 'halaman': '-',
                'deskripsi': f"Transaksi identik berulang {row['jumlah']}x pada tanggal {row['Tanggal']}",
                'detail': f"{row['Jenis Mutasi']} Rp{row['Mutasi']:,} — \"{row['Keterangan Transaksi']}\" "
                          f"— cek apakah ini transaksi rutin yang wajar berulang",
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
                            f'tanggal {streak[0]} s.d. {streak[-1]}. Gap belum tentu janggal — '
                            f'libur panjang, rekening musiman, atau pola bisnis tertentu bisa '
                            f'menjelaskannya. Bandingkan dengan pola bulan lain sebelum menyimpulkan.'
                        ),
                        'nilai_rp': None,
                    })
                streak = [d] if d is not None else []
                start = d
    return out


# ============================================================
# Helper hari libur — dipakai bersama oleh check 9 & 9b
# ============================================================

def _cek_hari_libur(tahun, bulan_num, tanggal):
    """
    Kembalikan (True, alasan) kalau tanggal jatuh di hari Minggu atau salah
    satu hari libur nasional tanggal-tetap (lihat LIBUR_TANGGAL_TETAP di
    atas — TIDAK mencakup libur lunar/hijriah seperti Lebaran/Nyepi/Imlek,
    yang tanggalnya berubah tiap tahun dan butuh referensi kalender
    eksternal). Kembalikan (False, None) kalau tanggal tidak valid atau
    bukan hari libur yang kita kenali.
    """
    try:
        d = datetime.date(int(tahun), bulan_num, tanggal)
    except ValueError:
        return False, None, None
    if d.weekday() == 6:
        return True, 'hari Minggu', d
    if (bulan_num, tanggal) in LIBUR_TANGGAL_TETAP:
        return True, 'tanggal merah (libur nasional)', d
    return False, None, d


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
            is_libur, alasan, d = _cek_hari_libur(tahun, bulan_num, tanggal)
            if is_libur:
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
# CHECK 9b — Transaksi RTGS di hari Minggu/hari libur
# ============================================================

def _check_rtgs_libur(transaksi_per_bulan, saldo_per_bulan):
    """
    RTGS diproses lewat sistem settlement BI-RTGS, yang TIDAK beroperasi
    di luar hari & jam kerja bank (bukan seperti setoran tunai via CDM
    yang bisa 24/7). Transaksi RTGS bertanggal Minggu/libur nasional
    karenanya lebih kuat indikasinya dibanding setoran tunai — bisa
    berarti dokumen diedit atau ada anomali pencatatan tanggal.

    Deteksi hanya untuk baris yang Keterangan Transaksi-nya eksplisit
    mengandung kata "RTGS". Extractor BCA saat ini tidak selalu
    membedakan RTGS dari jenis KR OTOMATIS lain (LLG, dst.) kalau kata
    "RTGS" tidak tercetak eksplisit di PDF — pada kasus begitu, check ini
    tidak akan menyala (bukan berarti tidak ada RTGS, tapi tidak
    teridentifikasi sebagai RTGS dari teksnya).
    """
    out = []
    for bulan, df in transaksi_per_bulan.items():
        if df is None or df.empty:
            continue
        tahun = saldo_per_bulan.get(bulan, {}).get('tahun')
        bulan_num = BULAN_TO_NUM.get(bulan)
        if not tahun or not bulan_num:
            continue

        mask = df['Keterangan Transaksi'].str.upper().str.contains('RTGS', na=False)
        for _, row in df[mask].iterrows():
            tanggal = int(row['Tanggal'])
            is_libur, alasan, d = _cek_hari_libur(tahun, bulan_num, tanggal)
            if is_libur:
                out.append({
                    'kategori': 'Transaksi RTGS di Hari Libur',
                    'tingkat': 'Tinggi',
                    'bulan': bulan, 'tanggal': tanggal, 'halaman': '-',
                    'deskripsi': f'Transaksi RTGS tercatat pada {alasan} ({d.strftime("%d/%m/%Y")}) — sistem BI-RTGS tidak beroperasi di luar hari/jam kerja bank',
                    'detail': f"{row['Jenis Mutasi']} Rp{int(row['Mutasi']):,} — \"{row['Keterangan Transaksi']}\"",
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

def _check_rasio_pajak_bunga(transaksi_per_bulan, saldo_per_bulan):
    """
    Bank umumnya memotong PPh Final 20% atas bunga tabungan/giro, jadi
    Pajak Bunga / Bunga seharusnya ≈0.20.

    Baris bunga & pajak dikenali lewat metadata '_bunga_pajak' dari extractor
    (lihat extractors/base.py): extractor menyebut kolom mana yang andal di
    format banknya beserta daftar nilainya, dan pencocokannya PERSIS SAMA —
    bukan "mengandung". Itu penting karena label "Bunga" bisa muncul pada
    transaksi tak terkait seperti "KARANGAN BUNGA" (papan bunga dukacita),
    yang tidak boleh ikut ke perhitungan ini.
    """
    out = []
    meta = saldo_per_bulan.get('_bunga_pajak') or {}
    kolom = meta.get('kolom') or 'Keterangan Transaksi'
    label_bunga = [str(x).strip().upper() for x in (meta.get('bunga') or [])]
    label_pajak = [str(x).strip().upper() for x in (meta.get('pajak') or [])]
    if not label_bunga and not label_pajak:
        # Extractor tidak memberi tahu bagaimana mengenali baris bunga/pajak
        # di format banknya — jangan menebak.
        return out

    for bulan, df in transaksi_per_bulan.items():
        if df is None or df.empty or kolom not in df.columns:
            continue
        kunci = df[kolom].astype(str).str.strip().str.upper()

        bunga_rows = df[(df['Jenis Mutasi'] == 'Kredit') & kunci.isin(label_bunga)]
        pajak_rows = df[(df['Jenis Mutasi'] == 'Debit') & kunci.isin(label_pajak)]

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
# CHECK 13 — Jadwal pendebetan biaya admin tidak sesuai ketentuan bank
# ============================================================

def _check_jadwal_biaya_adm(transaksi_per_bulan, saldo_per_bulan):
    """
    Cocokkan tanggal pendebetan biaya administrasi rekening dengan jadwal
    resmi bank yang bersangkutan.

    Pemeriksaan ini SEPENUHNYA digerakkan metadata '_biaya_admin' dari
    extractor (lihat extractors/base.py) — engine tidak tahu ketentuan bank
    mana pun. Sebelumnya aturan BCA ditulis langsung di sini, sehingga
    diam-diam ikut diberlakukan ke bank lain yang jadwalnya berbeda.

    Baris dikenali lewat kolom Nama yang dicocokkan PERSIS SAMA dengan
    'label_nama', bukan "mengandung" — supaya biaya lain yang namanya mirip
    (mis. "Biaya Administrasi Kartu Debit" di Mandiri, yang mengikuti tanggal
    ulang tahun kartu dan bukan jadwal rekening) tidak ikut terperiksa.

    Extractor yang tidak mengirim metadata ini membuat pemeriksaan dilewati.
    """
    out = []
    meta = saldo_per_bulan.get('_biaya_admin') or {}
    label = meta.get('label_nama')
    jadwal = meta.get('jadwal') or {}
    if not label or not jadwal:
        return out

    for bulan, df in transaksi_per_bulan.items():
        if df is None or df.empty:
            continue
        entri = jadwal.get(bulan)
        if not entri or entri.get('tanggal') is None:
            continue

        nama = df['Nama Pengirim/Penerima'].astype(str).str.strip()
        rows = df[(df['Jenis Mutasi'] == 'Debit') & (nama == label)]
        if rows.empty:
            continue

        tanggal_seharusnya = int(entri['tanggal'])
        aturan = entri.get('aturan') or f'tanggal {tanggal_seharusnya}'

        for _, row in rows.iterrows():
            tanggal_aktual = int(row['Tanggal'])
            if tanggal_aktual == tanggal_seharusnya:
                continue
            out.append({
                'kategori': 'Jadwal Biaya Admin Tidak Wajar',
                'tingkat': 'Sedang',
                'bulan': bulan, 'tanggal': tanggal_aktual, 'halaman': '-',
                'deskripsi': (
                    f'Biaya administrasi didebet tanggal {tanggal_aktual}, '
                    f'seharusnya {aturan}'
                ),
                'detail': f"Rp{int(row['Mutasi']):,} — \"{row['Keterangan Transaksi']}\"",
                'nilai_rp': int(row['Mutasi']),
            })
    return out


# ============================================================
# JEJAK CETAK — fakta per baris/halaman yang diserahkan extractor
# ============================================================

def _provenance(saldo_per_bulan) -> dict:
    """
    Ambil metadata '_provenance' (lihat kontrak di extractors/base.py).

    Extractor yang tidak mengirimnya menghasilkan dict kosong, sehingga
    pemeriksaan yang bergantung padanya tidak menemukan apa pun — dilewati,
    bukan menebak.
    """
    prov = saldo_per_bulan.get('_provenance') or {}
    return {'halaman': prov.get('halaman') or [], 'baris': prov.get('baris') or []}


# ============================================================
# CHECK 2 — Running balance tidak konsisten
# ============================================================

def _check_running_balance(prov, saldo_per_bulan):
    """
    Saldo berjalan yang TERCETAK di tiap baris harus sama dengan saldo
    tercetak sebelumnya ditambah mutasi baris-baris di antaranya.

    Rantainya di-reset tiap ganti blok laporan ('periode'): sebagian PDF
    menyusun halaman antar-bulan tidak kronologis, dan antar blok laporan
    boleh ada celah tanggal yang sah — saldo akhir blok sebelumnya tidak
    boleh "nyambung" begitu saja ke blok berikutnya.
    """
    out = []
    if not prov['baris']:
        return out

    periode_kini = object()   # sentinel, beda dari periode mana pun
    running = None
    for b in prov['baris']:
        if b.get('periode') != periode_kini:
            periode_kini = b.get('periode')
            # Saldo awal bulan pertama blok ini jadi titik mula kalau ada,
            # supaya baris PERTAMA pun ikut terperiksa.
            awal = saldo_per_bulan.get(f"_saldo_awal_{b.get('bulan')}")
            running = float(awal) if awal is not None else None

        mutasi = b.get('mutasi')
        tercetak = b.get('saldo_tercetak')
        if mutasi is None:
            continue
        if running is None:
            # Belum ada titik mula: pakai saldo tercetak pertama sebagai
            # patokan — jangan menuduh dari angka yang tidak diketahui.
            if tercetak is not None:
                running = float(tercetak)
            continue

        harapan = running + float(mutasi)
        if tercetak is None:
            running = harapan
            continue

        selisih = float(tercetak) - harapan
        if abs(selisih) > TOLERANSI_RUNNING_BALANCE:
            out.append({
                'kategori': 'Running Balance Tidak Konsisten',
                'tingkat': 'Tinggi',
                'bulan': b.get('bulan') or '-', 'tanggal': b.get('tanggal', '-'),
                'halaman': b.get('halaman', '-'),
                'deskripsi': 'Saldo berjalan tidak sesuai dengan mutasi tercatat',
                'detail': (
                    f'Perkiraan saldo {harapan:,.2f} vs tercetak {float(tercetak):,.2f} '
                    f'(selisih {selisih:,.2f})'
                    + (f' — baris: "{b["teks_mentah"]}"' if b.get('teks_mentah') else '')
                ),
                'nilai_rp': int(selisih),
            })
        # Selalu lanjut dari angka TERCETAK, bukan dari perkiraan: satu baris
        # yang meleset tidak boleh membuat semua baris sesudahnya ikut
        # dilaporkan meleset.
        running = float(tercetak)
    return out


# ============================================================
# CHECK 3 — Nomor halaman / periode tidak berurutan
# ============================================================

def _check_halaman_sequence(prov):
    """
    Nomor halaman yang TERCETAK harus naik satu per satu dalam satu blok
    laporan, dan total halamannya seragam.

    Dokumen yang tidak mencetak nomor halaman mengirim None — pemeriksaannya
    dilewati untuk dokumen itu, bukan dianggap lolos.
    """
    out = []
    sebelumnya = None
    periode_kini = object()
    for h in prov['halaman']:
        if h.get('periode') != periode_kini:
            periode_kini = h.get('periode')
            sebelumnya = None
        no = h.get('no_tercetak')
        if no is None:
            continue
        if sebelumnya is not None and no != sebelumnya + 1:
            out.append({
                'kategori': 'Halaman/Periode Tidak Berurutan',
                'tingkat': 'Tinggi' if no < sebelumnya else 'Sedang',
                'bulan': h.get('periode') or '-', 'tanggal': '-',
                'halaman': h.get('urut', '-'),
                'deskripsi': 'Nomor halaman yang tercetak tidak berurutan',
                'detail': (f'Setelah halaman {sebelumnya} muncul halaman {no} '
                           f'(halaman ke-{h.get("urut")} dalam berkas) — ada halaman '
                           f'yang hilang atau disisipkan'),
                'nilai_rp': None,
            })
        sebelumnya = no

    # Total halaman dibandingkan DI DALAM satu blok laporan, bukan lintas
    # berkas: satu PDF gabungan wajar memuat beberapa laporan yang masing-
    # masing punya jumlah halaman sendiri ("Page 1 of 4" lalu "Page 1 of 12").
    per_periode = {}
    for h in prov['halaman']:
        if h.get('total_tercetak') is not None:
            per_periode.setdefault(h.get('periode'), set()).add(h['total_tercetak'])
    for periode, total in per_periode.items():
        if len(total) > 1:
            out.append({
                'kategori': 'Halaman/Periode Tidak Berurutan',
                'tingkat': 'Sedang',
                'bulan': periode or '-', 'tanggal': '-', 'halaman': '-',
                'deskripsi': 'Total halaman yang tercetak berubah di tengah satu laporan',
                'detail': f'Nilai total halaman yang ditemukan: {sorted(total)}',
                'nilai_rp': None,
            })
    return out


# ============================================================
# CHECK 4 — Template halaman berbeda
# ============================================================

def _check_template_halaman(prov):
    """
    Halaman yang memuat transaksi tapi kehilangan baris header kolom.

    Hanya berlaku untuk dokumen yang memang mencetak header di setiap
    halaman; format yang mencetaknya sekali di awal laporan mengirim None dan
    tidak ikut diperiksa.
    """
    out = []
    for h in prov['halaman']:
        if h.get('ada_header_kolom') is not False or not h.get('jumlah_baris'):
            continue
        out.append({
            'kategori': 'Template Halaman Berbeda',
            'tingkat': 'Sedang',
            'bulan': h.get('periode') or '-', 'tanggal': '-',
            'halaman': h.get('urut', '-'),
            'deskripsi': 'Halaman berisi transaksi tapi header kolom standar tidak ditemukan',
            'detail': (f'{h.get("jumlah_baris")} transaksi di halaman ini, tapi baris '
                       f'header kolomnya hilang — bisa berarti halaman disisipkan dari '
                       f'sumber lain atau tata letaknya diubah'),
            'nilai_rp': None,
        })
    return out


# ============================================================
# CHECK 6 — Format nominal tidak konsisten
# ============================================================

def _check_format_nominal(prov):
    """
    Angka bergaya Eropa (titik ribuan, koma desimal) yang nyasar di antara
    baris berformat standar — pola yang lazim pada dokumen hasil edit.

    Hanya baris yang extractor-nya nyatakan berisi TEKS CETAK MESIN
    ('teks_mentah') yang diperiksa. Kolom keterangan yang memuat berita bebas
    dari nasabah sengaja tidak dikirim extractor: nasabah lazim menulis
    nominal bergaya Indonesia di berita transfer ("19.655.050"), dan itu
    bukan artefak dokumen.
    """
    out = []
    for b in prov['baris']:
        teks = b.get('teks_mentah')
        if not teks:
            continue
        if not FORMAT_ASING_RE.search(teks):
            continue
        out.append({
            'kategori': 'Format Nominal Tidak Konsisten',
            'tingkat': 'Sedang',
            'bulan': b.get('bulan') or '-', 'tanggal': b.get('tanggal', '-'),
            'halaman': b.get('halaman', '-'),
            'deskripsi': 'Ditemukan format angka non-standar (titik ribuan/koma '
                         'desimal) di baris transaksi',
            'detail': f'Baris: "{teks}" — kemungkinan hasil edit/OCR, nominal wajib '
                      f'dicek manual ke PDF asli',
            'nilai_rp': None,
        })
    return out


# ============================================================
# CHECK — Selisih dengan ringkasan resmi yang dilaporkan extractor
# ============================================================

def _check_checksum_extractor(saldo_per_bulan):
    """
    Cocokkan hasil ekstraksi dengan angka ringkasan resmi di PDF, memakai
    laporan yang disediakan extractor lewat metadata '_checksum'.

    Tetap bank-agnostik: engine hanya membaca struktur umum
    {label, expected, actual}; extractor yang tahu di mana ringkasan itu
    tercetak dan bagaimana membacanya. Extractor yang tidak menyediakannya
    cukup dilewati.
    """
    out = []
    laporan = saldo_per_bulan.get('_checksum') or []
    NAMA = {
        'n_debit': 'jumlah transaksi Debit',
        'n_credit': 'jumlah transaksi Kredit',
        'total_debit': 'total nominal Debit',
        'total_credit': 'total nominal Kredit',
        'closing': 'saldo akhir',
    }
    for per in laporan:
        label = per.get('label', '-')
        exp, act = per.get('expected') or {}, per.get('actual') or {}
        # Toleransi nominal boleh ditentukan extractor: sebagian extractor
        # menyimpan nominal dibulatkan ke rupiah penuh, sehingga totalnya
        # wajar melenceng beberapa rupiah dari angka resmi yang bersen.
        # Jumlah transaksi TIDAK ikut ditoleransi — itu harus sama persis.
        toleransi = float(per.get('toleransi') or 0.005)
        for kunci, nama in NAMA.items():
            e, a = exp.get(kunci), act.get(kunci)
            if e is None or a is None:
                continue
            batas = 0.005 if kunci.startswith('n_') else toleransi
            if abs(float(e) - float(a)) < batas:
                continue
            selisih = float(e) - float(a)
            angka = kunci.startswith('n_')
            out.append({
                'kategori': 'Selisih dengan Ringkasan PDF',
                'tingkat': 'Tinggi',
                'bulan': per.get('bulan', '-'), 'tanggal': '-', 'halaman': label,
                'deskripsi': f'{nama.capitalize()} hasil ekstraksi tidak sama dengan '
                             f'ringkasan resmi yang tercetak di PDF',
                'detail': (f'Ringkasan PDF: {int(e)}, hasil ekstraksi: {int(a)}'
                           if angka else
                           f'Ringkasan PDF: Rp{e:,.2f}, hasil ekstraksi: Rp{a:,.2f} '
                           f'(selisih Rp{selisih:,.2f})'),
                'nilai_rp': None if angka else abs(int(selisih)),
            })
    return out


# ============================================================
# CHECK — Peringatan dari extractor saat membaca PDF
# ============================================================

def _check_peringatan_extractor(saldo_per_bulan):
    """
    Teruskan hal-hal yang hanya diketahui extractor saat membaca PDF, lewat
    metadata '_peringatan' (lihat kontrak di extractors/base.py).

    Bedanya dengan '_checksum': checksum membandingkan ANGKA hasil parsing
    dengan angka ringkasan yang tercetak di PDF. Peringatan di sini soal
    KONDISI DOKUMENnya — halaman yang bukan bagian rekening ini, rentang
    tanggal yang tidak tercakup laporan mana pun, rantai saldo yang putus.
    Sebelumnya temuan seperti itu berhenti di dalam extractor dan tidak
    pernah sampai ke pemeriksa.

    Tetap bank-agnostik: engine hanya membaca strukturnya. Isi teksnya
    sepenuhnya dari extractor, karena hanya extractor yang tahu tata letak
    dokumennya.
    """
    out = []
    for p in saldo_per_bulan.get('_peringatan') or []:
        if not isinstance(p, dict):
            continue
        ringkas = str(p.get('ringkas') or '').strip()
        if not ringkas:
            continue
        tingkat = p.get('tingkat')
        if tingkat not in ('Tinggi', 'Sedang', 'Rendah'):
            # Extractor mengirim tingkat yang tidak dikenal: jangan diam-diam
            # diturunkan jadi 'Rendah' — peringatan yang salah tingkat lebih
            # berbahaya daripada peringatan yang kelewat menonjol.
            tingkat = 'Sedang'
        out.append({
            'kategori': 'Peringatan Pembacaan Dokumen',
            'tingkat': tingkat,
            'bulan': str(p.get('bulan') or '-'),
            'tanggal': '-',
            'halaman': str(p.get('halaman') or '-'),
            'deskripsi': ringkas,
            'detail': str(p.get('detail') or '').strip() or '-',
            'nilai_rp': None,
        })
    return out


# ============================================================
# CHECK — Urutan tanggal transaksi tidak wajar
# ============================================================

def _check_urutan_tanggal(transaksi_per_bulan):
    """
    Deteksi transaksi yang tanggalnya mundur dari baris sebelumnya.

    Rekening koran dicetak kronologis, jadi urutan seperti 01, 02, 03, 01,
    04 — atau transaksi tanggal 15 muncul setelah tanggal 20 — menandakan
    baris disisipkan atau dokumen disusun ulang.

    Pemeriksaan ini bergantung pada urutan baris hasil ekstraksi yang
    MENGIKUTI urutan cetak di PDF. Extractor yang mengurutkan ulang
    hasilnya akan membuat pemeriksaan ini tidak pernah menemukan apa pun.
    """
    out = []
    for bulan, df in transaksi_per_bulan.items():
        if df is None or df.empty or len(df) < 3:
            continue
        tanggal = [int(t) for t in df['Tanggal'].tolist()]
        maks = tanggal[0]
        for i in range(1, len(tanggal)):
            t = tanggal[i]
            if t < maks:
                out.append({
                    'kategori': 'Urutan Tanggal Tidak Wajar',
                    'tingkat': 'Tinggi',
                    'bulan': bulan, 'tanggal': t, 'halaman': '-',
                    'deskripsi': f'Transaksi tanggal {t} tercetak setelah transaksi tanggal {maks}',
                    'detail': (
                        f'Baris ke-{i + 1} di bulan {bulan} mundur {maks - t} hari dari '
                        f'tanggal tertinggi sebelumnya. Rekening koran biasanya kronologis, '
                        f'jadi urutan mundur bisa menandakan baris disisipkan — '
                        f'periksa halaman sumbernya.'
                    ),
                    'nilai_rp': None,
                })
            else:
                maks = t
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
