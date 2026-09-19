"""
keperluan.py — Pagar atas kolom "Keperluan" dan atas larangan memakainya
sebagai nama.

    python tests/keperluan.py

KENAPA TES INI ADA

Untuk transfer keluar lewat e-channel, dokumen BNI tidak mencetak nama
penerima sama sekali — yang ada hanya nomor rekening tujuan (28,7% baris BNI
di 45 PDF referensi, 623 nomor unik; BCA dan Mandiri nol). Berita yang
diketik nasabah adalah satu-satunya petunjuk terbaca manusia tentang urusan
apa itu, jadi ia dilaporkan.

Tapi berita itu BUKAN nama, dan godaan memakainya sebagai nama besar justru
karena ia terbaca enak. Harganya terukur: rekening 1050017365861 punya 12
transaksi dengan 7 berita berbeda, dan 37 dari 67 nomor yang bertransaksi
>=3 kali beritanya berganti-ganti. Dipakai sebagai nama, satu pihak pecah
jadi sebanyak beritanya — dan HHI di sheet Summary ikut salah ke arah
"terdiversifikasi" tanpa meninggalkan jejak.

Jadi yang dijaga di sini dua arah sekaligus: beritanya MUNCUL sebagai
keterangan pendamping, dan TIDAK PERNAH muncul sebagai nama.
"""

import os
import sys

SESAT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SESAT)

from engine.excel_builder import _teks_keperluan            # noqa: E402
from extractors.bni_nama import NamaLawanBNI                # noqa: E402

# Bentuk nyata dari berkas referensi, dipendekkan.
KET_ECHANNEL = ('TRF/PAY/TOP-UP ECHANNEL | PEMINDAHAN KE {rek} | '
                '0000000000000000 | {rek} {berita}')


def baris(rek: str, berita: str) -> dict:
    ket = KET_ECHANNEL.format(rek=rek, berita=berita)
    return {'nama': rek, 'keterangan': ket}


def periksa_berita_tidak_jadi_nama() -> list:
    """
    Nama pihak untuk baris e-channel tetap NOMOR REKENING, bukan beritanya.

    Ini pemeriksaan terpenting di berkas ini: begitu berita jadi nama, satu
    pihak pecah sebanyak beritanya dan HHI salah tanpa jejak.
    """
    masalah = []
    o = NamaLawanBNI()
    for berita in ('PEMBELIAN BBM ZONA', 'CICIL BBM ZONA 1',
                   'PEMBANGUNAN MESS TMA', 'kas rampdoor'):
        ket = KET_ECHANNEL.format(rek='1050017365861', berita=berita)
        nama = o._nama_lawan(ket, 'Debit')
        if nama != '1050017365861':
            masalah.append(f'berita {berita!r} jadi nama: {nama!r}')
    return masalah


def periksa_berita_tersering_dipilih() -> list:
    """
    Yang ditampilkan berita paling sering, dan jumlah sisanya ikut disebut.

    "Ikut disebut" bukan hiasan: menampilkan satu berita tanpa memberi tahu
    ada enam yang lain adalah pernyataan yang tidak benar tentang data.
    """
    masalah = []
    rek = '1050017365861'
    data = ([baris(rek, 'PEMBELIAN BBM ZONA 1')] * 5
            + [baris(rek, 'CICIL BBM ZONA 1')] * 2
            + [baris(rek, 'PELUNASAN BBM ZONA 1')])
    peta = NamaLawanBNI.keperluan_per_nomor(data)
    if peta.get(rek) != ('PEMBELIAN BBM ZONA 1', 2):
        masalah.append(f'yang tersering tidak terpilih: {peta.get(rek)!r}')

    teks = _teks_keperluan(rek, peta)
    if '+2 berita lain' not in (teks or ''):
        masalah.append(f'jumlah berita lain tidak disebut: {teks!r}')

    # Satu berita saja → tidak perlu embel-embel "+0".
    tunggal = NamaLawanBNI.keperluan_per_nomor([baris('123456789012', 'KAS D1')])
    teks = _teks_keperluan('123456789012', tunggal)
    if teks != 'KAS D1':
        masalah.append(f'berita tunggal diberi embel-embel: {teks!r}')
    return masalah


def periksa_seri_stabil() -> list:
    """
    Kalau dua berita sama seringnya, yang dipilih harus SELALU sama.

    Laporan yang sama wajib menghasilkan berkas yang sama. Tanpa pemecah
    seri yang pasti, `max()` mengikuti urutan sisipan dan dua kali jalan bisa
    berbeda — perbedaan yang tidak akan pernah terlihat sampai ada yang
    membandingkan dua unduhan.
    """
    masalah = []
    rek = '999888777666'
    a, b = baris(rek, 'ZZZ DULU'), baris(rek, 'AAA KEMUDIAN')
    maju = NamaLawanBNI.keperluan_per_nomor([a, b])
    balik = NamaLawanBNI.keperluan_per_nomor([b, a])
    if maju != balik:
        masalah.append(f'hasil bergantung urutan masukan: {maju!r} vs {balik!r}')
    if maju.get(rek, ('',))[0] != 'AAA KEMUDIAN':
        masalah.append(f'seri tidak dipecah menurut abjad: {maju.get(rek)!r}')
    return masalah


def periksa_penanda_untuk_yang_tanpa_berita() -> list:
    """
    Baris yang identitasnya nomor SELALU diberi isi, walau beritanya tidak
    ada.

    Separuh gunanya kolom ini adalah menandai bahwa dokumen tidak mencetak
    nama. Kalau selnya dibiarkan kosong, baris itu tidak bisa dibedakan dari
    baris bernama — penandanya hilang justru di kasus yang paling perlu
    ditandai. Di 45 PDF referensi ada 28 baris semacam itu.
    """
    masalah = []
    teks = _teks_keperluan('2056451380000748', {})
    if not teks or 'tidak mencetak nama' not in teks:
        masalah.append(f'nomor tanpa berita tidak ditandai: {teks!r}')

    # Label kanal dan angka bukan berita yang berguna.
    for berita in ('BNI DIRECT', '9112401526'):
        peta = NamaLawanBNI.keperluan_per_nomor([baris('1060004947878', berita)])
        if peta:
            masalah.append(f'{berita!r} diterima sebagai berita: {peta!r}')
    return masalah


def periksa_baris_bernama_dibiarkan_kosong() -> list:
    """
    Untuk baris yang SUDAH bernama, kolom ini kosong.

    Rekap itu per-pihak, berita itu per-transaksi: satu pihak punya banyak
    keperluan ("KAS KABANJAHE", "PEMBELIAN TERPAL BSL", …), jadi memilih
    salah satunya menyatakan sesuatu yang tidak benar. Tempatnya yang benar
    sudah ada di sheet Detail Transaksi.
    """
    masalah = []
    for nama in ('Bpk AMIR TARIGAN', 'PT HINO FINANCE INDONESIA',
                 'Tarik Tunai', 'BILL PAYMENT (MPN G2 IDR)'):
        teks = _teks_keperluan(nama, {nama: ('APA SAJA', 3)})
        if teks is not None:
            masalah.append(f'{nama!r} diberi kolom keperluan: {teks!r}')
    return masalah


def periksa_pemindahan_di_warkat() -> list:
    """
    Klausa "PEMINDAHAN KE <rek> <nama>" diupas juga saat berkepala warkat.

    Dulu cabang warkat mengembalikan segmen kedua UTUH, sehingga nama pihak
    tercetak "PEMINDAHAN KE 84705582 PT SHINHAN INDO FINANCE". Akibatnya
    bukan cuma salah cetak: dengan prefiks menempel, nama itu tidak akan
    pernah menyatu dengan ejaan lain dari pihak yang sama — dan
    "PT SHINHAN INDO FINANCE" memang muncul di dua berkas referensi dengan
    nomor rekening berbeda.
    """
    masalah = []
    o = NamaLawanBNI()
    uji = (
        ('TARIK CHQ/BG BN668833 | PEMINDAHAN KE 84705582 PT SHINHAN INDO FINANCE',
         'PT SHINHAN INDO FINANCE'),
        ('TARIK CHQ/BG BR510956 | PEMINDAHAN KE 1803835952 PT BUANA FINANCE TBK',
         'PT BUANA FINANCE TBK'),
        ('TARIK CHQ/BG CA836614 | PEMINDAHAN KE 8922813 KANTOR PUSAT PERUM DAMRI'
         ' | ERDY RINANTO/085695858417/CA836614',
         'KANTOR PUSAT PERUM DAMRI'),
        # Yang sudah benar sebelumnya harus TETAP benar.
        ('TRANSFER KE | PEMINDAHAN KE 443120278 Bpk AMIR TARIGAN | KAS KABANJAHE',
         'Bpk AMIR TARIGAN'),
        ('SETOR TUNAI | DAENG AJAM NURJAMIL | setoran', 'DAENG AJAM NURJAMIL'),
        ('TARIK TUNAI', 'Tarik Tunai'),
    )
    for ket, harap in uji:
        nama = o._nama_lawan(ket, 'Debit')
        if nama != harap:
            masalah.append(f'{ket[:50]!r} → {nama!r}, harusnya {harap!r}')
    return masalah


def main() -> int:
    masalah = []
    for nama, fungsi in (
        ('berita tidak pernah jadi nama', periksa_berita_tidak_jadi_nama),
        ('berita tersering dipilih & sisanya disebut',
         periksa_berita_tersering_dipilih),
        ('seri dipecah dengan cara yang stabil', periksa_seri_stabil),
        ('nomor tanpa berita tetap ditandai',
         periksa_penanda_untuk_yang_tanpa_berita),
        ('baris bernama dibiarkan kosong',
         periksa_baris_bernama_dibiarkan_kosong),
        ('klausa pemindahan diupas di kepala warkat',
         periksa_pemindahan_di_warkat),
    ):
        hasil = fungsi()
        print(f'{"BEDA" if hasil else "OK  "}  {nama}')
        masalah += hasil

    if masalah:
        print(f'\nRingkasan: {len(masalah)} masalah.')
        for m in masalah:
            print(f'  - {m}')
        return 1
    print('\nRingkasan: keperluan dilaporkan sebagai keterangan, bukan '
          'sebagai nama.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
