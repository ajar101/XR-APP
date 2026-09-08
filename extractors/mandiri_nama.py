"""
mandiri_nama.py — Ekstraksi nama lawan transaksi dari remark Bank Mandiri.

Kolom Remark pada PDF Kopra dan PDF Rekening Koran berasal dari mesin
pembukuan yang sama, sehingga tata bahasanya identik ("MCM InhouseTrf KE
<NAMA>", "CENAIDJA/<NAMA>", "Biaya Adm <cabang>", dst). Pipeline penamaan
karena itu ditaruh di satu tempat dan dipakai bersama — kalau disalin ke
tiap extractor, perbaikan pola pada satu format diam-diam tidak ikut ke
format lain.

Isi modul ini dipindahkan apa adanya dari mandiri_kopra.py; perilakunya
dijaga tetap sama persis (diverifikasi ulang atas seluruh PDF Kopra
referensi setelah pemindahan).
"""

import re




# Kode cabang/channel yang menempel di ekor remark dan bukan bagian nama.
_TAIL_CODE_RE = re.compile(r'(?:\s+\d{4,})+\s*$')
# Penanda batas akhir nama. "Transfer Fee"/"Clearing Fee" adalah label
# biaya yang MENYERTAI transfer, bukan penanda transaksi biaya.
_NAME_STOP_RE = re.compile(
    r'\s+(?:Transfer\s+(?:Fee|ATM)|Clearing\s+Fee|Deposit|Sweep)\b|\s+\d{5,}',
    re.IGNORECASE,
)
# Nomor rekening yang mengawali remark: "8205290229 - JUMA BERLIAN EXIM"
_LEAD_ACCT_RE = re.compile(r'^\d{6,}\s*-\s*')
# Kode cabang yang menempel tanpa spasi di ekor nama: "...EXIM PT12124"
_GLUED_CODE_RE = re.compile(r'(?<=[A-Za-z])\d{5,6}$')
# Ekor kode bank tujuan: "... - CENAIDJA12124"
_TAIL_BANK_RE = re.compile(r'\s*-\s*[A-Z]{4}IDJ[A-Z0-9]\d*\s*$')

def clean_nama(s: str) -> str:
    s = ' '.join((s or '').split())
    s = _LEAD_ACCT_RE.sub('', s)
    s = _TAIL_BANK_RE.sub('', s)
    s = _TAIL_CODE_RE.sub('', s)
    s = _GLUED_CODE_RE.sub('', s)
    s = s.strip(' .,-/')
    return ' '.join(s.split())

def cut_at_stop(s: str) -> str:
    m = _NAME_STOP_RE.search(s)
    return s[:m.start()] if m else s

def strip_berita(tail: str, prefix: str) -> str:
    """
    Buang berita transaksi yang menempel setelah nama.

    Kopra mencetak berita dua kali: versi terpotong ~20 karakter sebagai
    baris di ATAS tanggal, lalu versi penuh langsung setelah nama. Versi
    terpotong itu dipakai sebagai penanda di mana nama berakhir.
    """
    prefix = ' '.join((prefix or '').split())
    if len(prefix) < 12:
        return tail
    probe = prefix[:20].strip()
    idx = tail.find(probe)
    # idx > 0: berita ketemu SETELAH nama (idx == 0 berarti tak ada nama).
    return tail[:idx] if idx > 0 else tail

def cut_at_lowercase(s: str) -> str:
    """
    Potong di kata pertama yang bukan bagian nama.

    Nama lawan transaksi di rekening koran Kopra selalu dicetak huruf
    besar, sedangkan berita transaksi bercampur huruf kecil
    ("BSMDIDJA/YAYASAN HUMALAH BAIT AL HIKMAH Zakat Maal dan Infaq")
    atau diawali penomoran ("CENAIDJA/M. HARIS 1. Ritase LPG Brgkt").
    """
    out = []
    for tok in s.split():
        if re.search(r'[a-z]', tok) or re.match(r'^\d+\.?$', tok):
            break
        out.append(tok)
    return ' '.join(out) if out else s

def extract_nama(keterangan: str) -> str:
    """
    Pipeline berurutan: pola paling spesifik lebih dulu.

    Pengecekan biaya/admin sengaja ditempatkan PALING AKHIR — kalau
    ditaruh di awal, kata "Transfer Fee" yang menyertai hampir semua
    transfer InhouseTrf akan menelan nama aslinya.
    """
    if not keterangan:
        return '-'
    text = ' '.join(keterangan.split())

    # 1. MCM InhouseTrf KE/DARI <NAMA>  (pola terbesar, ~40% data)
    #    Transfer yang dikirim lewat layanan Cash-to-Cash menyelipkan kode
    #    kanal di antara label dan arahnya: "MCM InhouseTrf CS-CS KE <NAMA>".
    #    Tanpa mengizinkan kode itu, seluruh transaksi CS-CS jatuh ke fallback
    #    dan namanya jadi remark utuh (nomor referensi + berita + nama).
    m = re.search(r'InhouseTrf\s+(?:CS-CS\s+)?(?:KE|DARI)\s+(.+)', text, re.IGNORECASE)
    if m:
        tail = cut_at_stop(m.group(1))
        tail = strip_berita(tail, text[:m.start()])
        nama = clean_nama(cut_at_lowercase(tail))
        if nama:
            return nama

    # 2. Transfer antar bank: <KODEBANK>IDJ?/<NAMA>  (~15%).
    #    Kode bank umumnya berakhiran 'A' (CENAIDJA) tapi ada juga yang
    #    berakhiran angka (BUSTIDJ1, DANAIDJ1), jadi jangan dipatok 'IDJA'.
    m = re.search(r'[A-Z]{4}IDJ[A-Z0-9]/(.+)', text)
    if m:
        tail = re.split(r'\s*\d{5,}', m.group(1))[0]
        nama = clean_nama(cut_at_lowercase(tail))
        if nama:
            return nama

    # 3. Transfer masuk/keluar antar bank: "<NAMA> - <kode> Trf Inw CN <BANK>"
    m = re.search(r'^(.*?)\s+-\s+\d{2,3}\s+Trf\s+(?:Inw|Outw)\b', text, re.IGNORECASE)
    if m:
        head = m.group(1)
        # Berita/kode invoice kadang mengawali; buang token berkode di depan.
        toks = head.split()
        while toks and (re.search(r'[/\\]', toks[0]) or re.search(r'\d', toks[0])):
            toks.pop(0)
        nama = clean_nama(cut_at_lowercase(' '.join(toks)))
        if nama:
            return nama

    # 4. Transfer ATM: "DARI/KE <NAMA> Transfer ATM <kode terminal>"
    m = re.match(r'^(?:DARI|KE)\s+(.+?)\s+Transfer\s+ATM\b', text, re.IGNORECASE)
    if m:
        nama = clean_nama(m.group(1))
        if nama:
            return nama

    # 4b. Pemindahbukuan berlabel: "Transfer - <NAMA><kode cabang>". Tanpa ini
    #     nama yang sama terpecah dua di rekap — sekali dengan awalan
    #     "Transfer - ", sekali tanpa.
    m = re.match(r'^Transfer\s+-\s+(.+)', text, re.IGNORECASE)
    if m:
        nama = clean_nama(cut_at_stop(m.group(1)))
        if nama and not re.fullmatch(r'[\d\s]+', nama):
            return nama

    # 5. Kliring keluar: MCM Outw CN <NAMA> ... Clearing Fee
    m = re.search(r'Outw\s+(?:CN|DN)\s+(.+)', text, re.IGNORECASE)
    if m:
        nama = clean_nama(cut_at_stop(m.group(1)))
        if nama:
            return nama

    # Nomor referensi transaksi sebelumnya kadang tersisa sebagai pecahan
    # pendek di awal remark (mis. "02 Clearing Fee ..."), karena Kopra
    # mencetak ekor nomor referensi di kolom Remark. Untuk pencocokan
    # kategori, pecahan itu diabaikan — teks aslinya tidak diubah.
    text = re.sub(r'^(?:\d{1,4}\s+)+', '', text) or text
    # Bentuk lain dari sisa nomor referensi yang sama: satu token panjang
    # "<angka>/<kode>" di awal remark, mis.
    # "00000000002/G299105 Biaya Adm 12001". Tanpa dibuang, baris biaya
    # admin tidak dikenali dan namanya jadi nomor referensi.
    text = re.sub(r'^\d{6,}/[A-Z0-9]+\s+', '', text) or text
    upper = text.upper()

    # Transaksi kartu debit / ATM / EDC:
    #   "<terminal> /<urut>/<TIPE>- <merchant> <no kartu> <lokasi><cabang>"
    # Nama merchant atau lokasi ATM ada SETELAH nomor kartu 16 digit.
    m = re.search(r'/(VAP|ATM|JPN|LNK|ATB|CB)-\s*(.+)', text)
    if m:
        tail = m.group(2)
        parts = re.split(r'\b\d{16}\b', tail)
        cand = parts[-1] if len(parts) > 1 else tail
        cand = re.sub(r'ID\d{4,6}\s*$', '', cand.strip())
        # Nama merchant sering bercampur huruf besar-kecil, jadi di sini
        # TIDAK dipotong di huruf kecil seperti pada nama perorangan.
        cand = clean_nama(cand)
        if re.search(r'[A-Za-z]{2,}', cand):
            return cand
        return {
            'VAP': 'Pembayaran EDC/Merchant',
            'ATM': 'Transaksi ATM',
        }.get(m.group(1), 'Transaksi Kartu Debit')

    # Baris biaya SKN/RTGS: hanya nomor referensi + kode cabang, tanpa nama
    # ("20260301BMRIIDJA010O9 933021416 99102").
    # Satu token berita dari baris sebelumnya kadang ikut di depan
    # ("Angsuran99102 20260310BMRIIDJA010O9 935480393 99102").
    if re.match(r'^(?:\S+\s+)?\d{6,}[A-Z]{4}IDJ[A-Z0-9]\w*(?:\s+\d+)*\s*$', text.strip()):
        return 'Biaya Transfer Antar Bank'

    # Baris biaya RTGS: berlabel di depan, lalu nomor referensi + kode cabang
    # yang menempel nama REKENING SENDIRI ("RTGS Fee 202502271548956721
    # 99102OPTIMA PETRO ENERGI"). Nama di ekor itu pemilik rekening, bukan
    # lawan transaksi, jadi yang dipakai label biayanya — disamakan dengan
    # baris biaya SKN/RTGS di atas supaya seluruh biaya transfer antar bank
    # berkumpul jadi satu di rekap.
    if re.match(r'^RTGS\s+Fee\b', text, re.IGNORECASE):
        return 'Biaya Transfer Antar Bank'

    # 6. Pembayaran tagihan (UBP). Remark UBP hanya berisi kode biller,
    #    tidak memuat nama — jadi dipakai label kategori.
    if re.match(r'^UBP\d', text.strip(), re.IGNORECASE):
        return 'Pembayaran Tagihan (UBP)'

    # Instruksi bayar lewat kanal host-to-host: remark HANYA berisi nomor
    # referensi berawalan H, nomor pelanggan, dan kode cabang
    # ("H000072459640331528602 2562569 99102 99102") — sumbernya memang tidak
    # memuat nama lawan transaksi sama sekali. Tanpa label ini setiap baris
    # jadi "penerima" yang berbeda: satu rekening referensi menghasilkan 102
    # penerima palsu dan rekap debit-nya tidak bisa dibaca. Nomor
    # referensinya tetap utuh di kolom Keterangan Transaksi.
    if re.match(r'^H\d{15,}(?:\s+\d+)*\s*$', text.strip()):
        return 'Transaksi Host-to-Host (H2H)'

    # Transfer masuk lewat jaringan PRIMA: remark hanya memuat nomor rekening
    # tujuan + kode terminal, tanpa nama pengirim
    # ("PRMA CR Transf 1480099034756 0232058355580055 S1ACIB9505/297987
    # /PRM-KBB99105"). Sama seperti H2H: dikelompokkan, bukan dipecah per
    # nomor terminal.
    if re.match(r'^PRMA\s+(?:CR|DB)\s+Transf\b', text, re.IGNORECASE):
        return 'Transfer via Jaringan PRIMA'

    # 7. Kategori tetap.
    if re.match(r'^DARI\s+\d+\s+KE\s+\d+', text.strip(), re.IGNORECASE):
        return 'Pindah Buku / Sweep'
    if 'MONTHLY CARD CHARGE' in upper:
        return 'Biaya Kartu Bulanan'
    # Deposito: lawan transaksinya rekening deposito milik sendiri, bukan
    # pihak lain. Dicek SEBELUM aturan warkat di bawah karena penempatannya
    # sering lewat warkat cek ("CK 512721-PENEMPATAN DEPOSITO Buka Deposito").
    if re.search(r'\bBUKA\s+DEPOSITO\b', upper):
        return 'Penempatan Deposito'
    if re.search(r'\bCAIR\s+DEPOSITO\b', upper):
        return 'Pencairan Deposito'
    # Tarik/Setor tunai mencantumkan nama pemegang rekening setelah labelnya
    # ("PEMBAYARAN TPP Tarik Tunai JUMA BERLIAN EXIM 12124"). Nama itu yang
    # dipakai; label hanya jadi cadangan kalau tidak ada nama menyusul.
    # ".*" di depan memaksa kecocokan TERAKHIR — remark kadang mengulang
    # labelnya ("TARIK TUNAI Tarik Tunai <NAMA> 12124").
    m = re.match(r'.*\b(?:Tarik|Setor)\s+Tunai\s+(.+)', text, re.IGNORECASE)
    if m:
        nama = clean_nama(cut_at_stop(m.group(1)))
        # Sebagian setoran/tarikan tidak menyebut nama sama sekali, hanya kode
        # cabang atau nomor warkat ("LPG Setor Tunai 14807", "CK 027137-JA
        # 027137 Tarik Tunai 00027137 12218"). Angka telanjang bukan nama —
        # kalau dipakai, tiap setoran tunai jadi "pengirim" berbeda dan
        # rekap per nama ikut melenceng. Jatuhkan ke label transaksinya.
        if nama and not re.fullmatch(r'[\d\s]+', nama):
            return nama
    if 'TARIK TUNAI' in upper or 'PENARIKAN TUNAI' in upper:
        return 'Tarik Tunai'
    if 'SETOR TUNAI' in upper or 'SETORAN TUNAI' in upper:
        return 'Setor Tunai'
    if re.match(r'^CLEARING\s+FEE\b', text.strip(), re.IGNORECASE):
        return 'Biaya Kliring'
    # Warkat cek / bilyet giro yang bukan tarik tunai maupun deposito: remark
    # hanya memuat nomor warkat + berita bebas ("CK 459657-operasional
    # 00459657 12926", "BG 000594-LPG Setor Kliring 10000594 14807), tanpa
    # nama pihak lawan. Nomor warkatnya unik per transaksi, jadi kalau
    # dipakai sebagai nama, tiap warkat jadi satu "pihak" tersendiri.
    if re.match(r'^(?:CK|BG)\s*\d{4,}\s*-', text.strip(), re.IGNORECASE):
        return 'Warkat Cek/Bilyet Giro'

    # 8. Biaya/bunga/pajak — PALING AKHIR, dan hanya kalau remark memang
    #    berdiri sendiri sebagai transaksi biaya (bukan sekadar memuat
    #    kata "Fee" sebagai pelengkap transfer).
    if re.match(r'^BUNGA\b', upper):
        return 'Bunga'
    if re.match(r'^PAJAK\b', upper):
        return 'Pajak'
    if re.match(r'^(?:BIAYA\s+ADM|ADM)\b', upper):
        return 'Biaya Administrasi'
    if re.match(r'^BIAYA\s+MATERAI\b', upper) or re.match(r'^MATERAI\b', upper):
        return 'Biaya Materai'

    # 9. Fallback: remark yang sudah dibersihkan, apa adanya.
    #    Bukan "-" (buang informasi) dan bukan "Biaya Admin" (salah label).
    fallback = clean_nama(text)
    return fallback if fallback else '-'
