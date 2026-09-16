"""
regresi.py — Tes regresi ekstraksi atas seluruh PDF referensi.

Kenapa ada: perubahan pada satu extractor (atau pada pipeline yang dipakai
bersama, mis. extractors/mandiri_nama.py) bisa diam-diam mengubah hasil
bank/format LAIN. Selama ini pembandingan sebelum-sesudah dilakukan manual
setiap kali — cara itu tidak bisa diandalkan terus-menerus.

Cara pakai:

    python tests/regresi.py                 # bandingkan dengan snapshot
    python tests/regresi.py --rekam         # tulis ulang snapshot (setelah
                                            #   perubahan diperiksa & disengaja)
    python tests/regresi.py --hanya koran   # batasi ke berkas yang namanya cocok

Keluar dengan kode 1 kalau ada beda — jadi bisa dipakai di CI.

Snapshot disimpan satu berkas JSON per PDF di tests/snapshot/, dengan urutan
kunci yang stabil, supaya `git diff` langsung menunjukkan BARIS MANA yang
berubah — bukan cuma "ada yang berubah". Isinya sengaja lengkap sampai
tingkat baris: satu nominal yang bergeser harus terlihat, bukan tersamar
oleh angka ringkasan.

Snapshot BUKAN pernyataan bahwa hasilnya benar. Ia hanya merekam hasil hari
ini. Kebenarannya dijaga oleh checksum extractor (validate(), yang ikut
direkam di sini) yang mencocokkan hasil parsing dengan angka resmi yang
tercetak di PDF-nya sendiri.
"""

import argparse
import json
import os
import sys

SESAT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SESAT)

from extractors.registry import BANK_REGISTRY   # noqa: E402
from engine.anomaly_detector import detect_anomalies   # noqa: E402

REFERENSI = os.path.join(SESAT, 'references')
SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'snapshot')

# Bank pemilik tiap PDF referensi ditentukan dari awalan nama berkasnya.
# Kalau ada awalan baru, tambahkan di sini — berkas yang tidak dikenali
# dilaporkan, bukan dilewati diam-diam.
AWALAN_BANK = {
    'BCA_': 'bca',
    'mandiri_': 'mandiri',
    'bni_': 'bni',
    'BNI_': 'bni',
}


def bank_dari_nama(nama: str) -> str | None:
    for awalan, bank in AWALAN_BANK.items():
        if nama.startswith(awalan):
            return bank
    return None


def rekam_satu(path: str, bank: str) -> dict:
    """Jalankan extractor atas satu PDF, kembalikan snapshot hasilnya."""
    ExtractorClass = BANK_REGISTRY[bank]['extractor']
    ex = ExtractorClass(path)

    saldo = ex.extract_saldo()
    transaksi = ex.extract_transaksi()

    bulan_urut = sorted(
        (b for b in saldo if not b.startswith('_')),
        key=lambda b: (str(saldo[b].get('tahun')), b),
    )

    hasil = {
        'berkas': os.path.basename(path),
        'bank': bank,
        'format': getattr(ex, 'format_type', None),
        'identitas': {
            'no_rekening': saldo.get('_no_rekening'),
            'nama_pemilik': saldo.get('_nama_pemilik'),
            'jenis_rekening': saldo.get('_jenis_rekening'),
        },
        'bulan': [],
        'transaksi': [],
        'saldo_harian': [],
        'temuan': [],
    }

    for b in bulan_urut:
        df = saldo[b]['df']
        tx = transaksi.get(b)
        hasil['bulan'].append({
            'bulan': b,
            'tahun': saldo[b].get('tahun'),
            'saldo_awal': saldo.get(f'_saldo_awal_{b}'),
            'jumlah_hari': int(len(df)),
            'jumlah_transaksi': int(len(tx)) if tx is not None else 0,
            'total_debit': _jumlah(tx, 'Debit'),
            'total_kredit': _jumlah(tx, 'Kredit'),
        })
        for _, r in df.iterrows():
            hasil['saldo_harian'].append(
                [b, int(r['Tanggal']), _angka(r['Saldo Akhir Harian'])]
            )

    # Urutan transaksi ikut direkam apa adanya: urutan cetak adalah salah satu
    # hal yang diperiksa indikator "Urutan Tanggal Tidak Wajar", jadi
    # perubahan urutan pun harus kelihatan sebagai regresi.
    for b in bulan_urut:
        tx = transaksi.get(b)
        if tx is None:
            continue
        for _, r in tx.iterrows():
            hasil['transaksi'].append([
                b, int(r['Tanggal']), r['Jenis Mutasi'], _angka(r['Mutasi']),
                r['Nama Pengirim/Penerima'], r['Keterangan Transaksi'],
            ])

    # Metadata ketentuan bank yang dikirim extractor ke engine.
    hasil['metadata'] = {
        'biaya_admin': saldo.get('_biaya_admin'),
        'bunga_pajak': saldo.get('_bunga_pajak'),
        'peringatan': saldo.get('_peringatan') or [],
    }

    # Temuan Sheet "Indikasi Kejanggalan". Ikut direkam karena inilah yang
    # dibaca pemeriksa, dan sebagian pemeriksaannya TIDAK tersentuh data
    # ekstraksi di atas — ia membaca ulang PDF mentah. Tanpa direkam,
    # perubahan pada pemeriksaan itu (mis. temuan palsu yang hilang, atau
    # temuan asli yang ikut hilang) tidak akan tertangkap tes ini.
    # Diurutkan kanonik, BUKAN mengikuti urutan tampil di Sheet 9. Urutan
    # tampil ditentukan sort eksplisit di detect_anomalies() dan bersifat
    # kosmetik; kalau ikut direkam, menambah satu pemeriksaan saja menggeser
    # posisi puluhan temuan lain dan diff-nya jadi tidak terbaca — persis
    # yang membuat tes regresi diabaikan orang.
    temuan = [
        [f['kategori'], f['tingkat'], str(f['bulan']), str(f['tanggal']),
         str(f['halaman']), f['deskripsi'], f['detail'], f['nilai_rp']]
        for f in detect_anomalies(path, saldo, transaksi, bank_name=prefix(ex))
    ]
    hasil['temuan'] = sorted(temuan, key=lambda t: [str(x) for x in t])

    if hasattr(ex, 'validate'):
        lap = ex.validate()
        hasil['checksum'] = {
            'ok': bool(lap.get('ok')),
            'duplikat_digabung': lap.get('duplikat_digabung'),
            'periode': [
                {'label': p.get('label'), 'bulan': p.get('bulan'),
                 'checks': p.get('checks')}
                for p in lap.get('periods', [])
            ],
            'peringatan': list(lap.get('warnings', [])),
        }
    return hasil


def prefix(ex) -> str:
    """Nama bank yang dipakai engine (sama dengan yang dikirim app.py)."""
    return ex.get_file_prefix() if hasattr(ex, 'get_file_prefix') else 'BANK'


def _jumlah(df, jenis: str):
    if df is None or df.empty:
        return 0
    return _angka(df.loc[df['Jenis Mutasi'] == jenis, 'Mutasi'].sum())


def _angka(v):
    """Bulatkan ke 2 desimal supaya galat float tidak jadi beda palsu."""
    if v is None:
        return None
    return round(float(v), 2)


# Daftar yang tiap barisnya ditulis SATU BARIS penuh, bukan dipecah per
# elemen seperti bawaan json.dump(indent=...). Dengan begitu satu transaksi
# yang berubah muncul sebagai satu baris diff — bukan delapan.
DAFTAR_SEBARIS = ('transaksi', 'saldo_harian', 'temuan')


def tulis(hasil: dict, path: str) -> None:
    baris = ['{']
    kunci = list(hasil)
    for i, k in enumerate(kunci):
        koma = ',' if i < len(kunci) - 1 else ''
        v = hasil[k]
        if k in DAFTAR_SEBARIS:
            baris.append(f' {json.dumps(k, ensure_ascii=False)}: [')
            for j, row in enumerate(v):
                akhir = ',' if j < len(v) - 1 else ''
                baris.append(f'  {json.dumps(row, ensure_ascii=False)}{akhir}')
            baris.append(f' ]{koma}')
        else:
            teks = json.dumps(v, indent=1, ensure_ascii=False)
            teks = '\n'.join(
                (line if idx == 0 else ' ' + line)
                for idx, line in enumerate(teks.split('\n'))
            )
            baris.append(f' {json.dumps(k, ensure_ascii=False)}: {teks}{koma}')
    baris.append('}')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(baris) + '\n')


def beda(lama: dict, baru: dict) -> list:
    """Bandingkan dua snapshot, kembalikan daftar beda yang bisa dibaca."""
    pesan = []

    for kunci in ('bank', 'format', 'identitas', 'metadata'):
        pesan += _beda_dict(lama.get(kunci), baru.get(kunci), kunci)

    if lama.get('checksum') != baru.get('checksum'):
        l, b = lama.get('checksum') or {}, baru.get('checksum') or {}
        if l.get('ok') != b.get('ok'):
            pesan.append(f"checksum.ok: {l.get('ok')} -> {b.get('ok')}")
        if l.get('periode') != b.get('periode'):
            pesan.append('checksum.periode berubah')
        for w in set(b.get('peringatan', [])) - set(l.get('peringatan', [])):
            pesan.append(f'peringatan BARU: {w}')
        for w in set(l.get('peringatan', [])) - set(b.get('peringatan', [])):
            pesan.append(f'peringatan HILANG: {w}')

    pesan += _beda_daftar(lama.get('bulan', []), baru.get('bulan', []), 'bulan')
    pesan += _beda_daftar(lama.get('saldo_harian', []), baru.get('saldo_harian', []),
                          'saldo_harian')
    pesan += _beda_daftar(lama.get('transaksi', []), baru.get('transaksi', []),
                          'transaksi')
    pesan += _beda_daftar(lama.get('temuan', []), baru.get('temuan', []), 'temuan')
    return pesan


def _beda_dict(lama, baru, nama: str) -> list:
    """
    Bandingkan dua nilai, laporkan hanya BAGIAN yang berubah.

    Kalau seluruh dict ikut dicetak, pesan bedanya jadi berparagraf-paragraf
    dan yang berubah justru tidak kelihatan — tes yang pesannya tidak terbaca
    sama saja tidak dipakai.
    """
    if lama == baru:
        return []
    if not isinstance(lama, dict) or not isinstance(baru, dict):
        return [f'{nama}: {_ringkas(lama)} -> {_ringkas(baru)}']
    pesan = []
    for k in sorted(set(lama) | set(baru)):
        if lama.get(k) == baru.get(k):
            continue
        if k not in lama:
            pesan.append(f'{nama}.{k}: BARU = {_ringkas(baru[k])}')
        elif k not in baru:
            pesan.append(f'{nama}.{k}: DIHAPUS (dulu {_ringkas(lama[k])})')
        else:
            pesan.append(f'{nama}.{k}: {_ringkas(lama[k])} -> {_ringkas(baru[k])}')
    return pesan


def _ringkas(v, maks: int = 160) -> str:
    teks = json.dumps(v, ensure_ascii=False, sort_keys=True)
    return teks if len(teks) <= maks else teks[:maks] + f'... (+{len(teks) - maks} char)'


def _beda_daftar(lama: list, baru: list, nama: str, contoh: int = 5) -> list:
    if lama == baru:
        return []
    pesan = []
    if len(lama) != len(baru):
        pesan.append(f'{nama}: jumlah baris {len(lama)} -> {len(baru)}')
    n = 0
    for i, (x, y) in enumerate(zip(lama, baru)):
        if x == y:
            continue
        n += 1
        if n <= contoh:
            pesan.append(f'  {nama}[{i}]:\n      lama: {x}\n      baru: {y}')
    if n > contoh:
        pesan.append(f'  ... dan {n - contoh} baris {nama} lain berbeda')
    return pesan


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rekam', action='store_true',
                   help='tulis ulang snapshot, jangan membandingkan')
    p.add_argument('--hanya', default='',
                   help='batasi ke berkas yang namanya memuat teks ini')
    args = p.parse_args()

    os.makedirs(SNAPSHOT, exist_ok=True)
    berkas = sorted(f for f in os.listdir(REFERENSI) if f.lower().endswith('.pdf'))
    if args.hanya:
        berkas = [f for f in berkas if args.hanya in f]
    if not berkas:
        print('Tidak ada PDF referensi yang cocok.')
        return 1

    n_beda = n_gagal = n_lewat = 0
    for nama in berkas:
        bank = bank_dari_nama(nama)
        if bank is None:
            print(f'?  {nama}: awalan nama tidak dikenali, tidak diuji '
                  f'(daftarkan di AWALAN_BANK kalau memang perlu diuji)')
            n_lewat += 1
            continue
        if not BANK_REGISTRY[bank].get('enabled'):
            print(f'-  {nama}: bank {bank} nonaktif di registry, dilewati')
            n_lewat += 1
            continue

        path_pdf = os.path.join(REFERENSI, nama)
        path_snap = os.path.join(SNAPSHOT, nama.replace('.pdf', '.json'))
        try:
            hasil = rekam_satu(path_pdf, bank)
        except Exception as e:
            print(f'!! {nama}: ekstraksi GAGAL — {type(e).__name__}: {e}')
            n_gagal += 1
            continue

        if args.rekam:
            tulis(hasil, path_snap)
            print(f'.. {nama}: snapshot ditulis')
            continue

        if not os.path.exists(path_snap):
            print(f'?  {nama}: belum punya snapshot — jalankan --rekam')
            n_lewat += 1
            continue

        with open(path_snap, encoding='utf-8') as f:
            lama = json.load(f)
        pesan = beda(lama, hasil)
        if pesan:
            n_beda += 1
            print(f'\nBEDA  {nama}')
            for m in pesan:
                print(f'   {m}')
        else:
            print(f'OK    {nama}')

    print()
    if args.rekam:
        print(f'Snapshot direkam untuk {len(berkas) - n_lewat - n_gagal} berkas.')
        return 1 if n_gagal else 0

    print(f'Ringkasan: {len(berkas) - n_beda - n_gagal - n_lewat} sama, '
          f'{n_beda} berbeda, {n_gagal} gagal, {n_lewat} dilewati.')
    if n_beda or n_gagal:
        print('Periksa bedanya. Kalau perubahannya memang disengaja dan sudah '
              'diperiksa satu per satu, rekam ulang dengan --rekam.')
    return 1 if (n_beda or n_gagal) else 0


if __name__ == '__main__':
    sys.exit(main())
