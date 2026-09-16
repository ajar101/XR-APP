"""
tugas.py — Pekerjaan ekstraksi, terpisah dari lapisan web.

Modul ini memuat SATU jalur ekstraksi yang dipakai dua cara:

  MODE LANGSUNG   app.py memanggilnya di dalam request (seperti sebelumnya)
  MODE ANTREAN    worker RQ memanggilnya di proses lain (lihat worker.py)

Ditulis sekali dan dipakai keduanya dengan sengaja. Kalau dua mode itu punya
implementasi sendiri-sendiri, cepat atau lambat keduanya berbeda — dan yang
berbeda adalah perilaku pada berkas nasabah, tempat perbedaan paling tidak
boleh terjadi. Yang membedakan kedua mode hanya SIAPA yang memanggil, bukan
APA yang dikerjakan.

Fungsi di sini tidak menyentuh `request`, `current_user`, atau apa pun dari
Flask: worker RQ berjalan tanpa konteks request, jadi semua yang dibutuhkan
harus datang lewat argumen.
"""

import logging
import os
import shutil
import time

from extractors.registry import get_extractor
from extractors.pdf_utils import is_probably_scanned
from engine.excel_builder import create_excel
from engine.multi_pdf_merger import merge_extractions, MergeValidationError

log = logging.getLogger(__name__)

# Berapa lama berkas hasil menunggu diunduh sebelum dihapus sendiri.
#
# Inilah kebijakan retensinya. Di mode langsung, laporan tidak pernah
# meninggalkan memori proses sehingga tidak ada yang perlu kedaluwarsa. Di
# mode antrean, worker dan pengunduh adalah proses BERBEDA sehingga hasilnya
# harus tersimpan — dan begitu tersimpan, ia wajib punya batas umur. Nilai
# ini diteruskan sebagai `result_ttl` RQ, jadi Redis sendiri yang
# menghapusnya; tidak ada job pembersih yang bisa lupa dijalankan.
TTL_HASIL_DETIK = int(os.environ.get('XR_TTL_HASIL', '3600'))

# Umur maksimum folder unggahan yatim (job yang worker-nya mati di tengah
# jalan). Disapu saat job baru masuk.
UMUR_YATIM_DETIK = 6 * 3600


class GagalEkstraksi(Exception):
    """
    Kegagalan yang PESANNYA memang untuk dibaca pemakai.

    Dibedakan dari exception lain: pesan di sini sudah dirancang menjelaskan
    apa yang salah dan apa yang harus dilakukan (PDF hasil scan, format belum
    didukung, bulan bentrok). Exception lain tidak ditampilkan ke pemakai
    karena isinya bisa memuat potongan data dokumen.
    """


def sapu_yatim(folder_unggahan: str, umur_detik: int = UMUR_YATIM_DETIK) -> int:
    """
    Hapus folder unggahan yang ditinggalkan job yang tidak selesai.

    Worker menghapus foldernya sendiri di blok `finally`, tapi `finally` tidak
    jalan kalau prosesnya mati mendadak (OOM, mesin di-restart). Tanpa sapuan
    ini, PDF rekening nasabah bisa tertinggal di disk tanpa batas waktu.
    """
    if not os.path.isdir(folder_unggahan):
        return 0
    batas = time.time() - umur_detik
    dihapus = 0
    for nama in os.listdir(folder_unggahan):
        path = os.path.join(folder_unggahan, nama)
        try:
            if os.path.isdir(path) and os.path.getmtime(path) < batas:
                shutil.rmtree(path, ignore_errors=True)
                dihapus += 1
        except OSError:
            continue
    if dihapus:
        log.warning('Menyapu %d folder unggahan yatim dari %s — ada job yang '
                    'tidak selesai dengan bersih.', dihapus, folder_unggahan)
    return dihapus


def proses_ekstraksi(bank_code: str, berkas: list, folder_job: str) -> dict:
    """
    Uraikan PDF dan bangun laporan Excel.

    Args:
        bank_code:  kode bank di extractors/registry.py
        berkas:     list (path_di_disk, nama_asli) — nama asli dipakai untuk
                    pesan kesalahan, karena nama di disk sudah diberi awalan.
        folder_job: folder tempat PDF-nya singgah; DIHAPUS sebelum fungsi ini
                    kembali, apa pun hasilnya.

    Returns:
        dict {'nama_file': str, 'xlsx': bytes, 'no_rekening': str,
              'peringatan': list[str]}

    Raises:
        GagalEkstraksi: kegagalan yang pesannya untuk pemakai.
    """
    import io

    try:
        try:
            ExtractorClass = get_extractor(bank_code)
        except ValueError as e:
            raise GagalEkstraksi(str(e)) from e

        per_file_results = []
        extractor_pertama = None
        laporan_checksum = []
        peringatan = []

        for path, nama_asli in berkas:
            # Cek dulu sebelum ekstraksi (yang bisa makan puluhan detik untuk
            # PDF ratusan halaman) — PDF hasil scan/foto tidak punya layer
            # teks sama sekali, jadi extractor apa pun pasti gagal. Lebih
            # baik gagal cepat dengan pesan jelas daripada nunggu extractor
            # jalan penuh lalu gagal dengan pesan generik.
            if is_probably_scanned(path):
                raise GagalEkstraksi(
                    f"File '{nama_asli}' terdeteksi sebagai PDF hasil scan/foto "
                    f"(gambar), bukan PDF teks asli dari sistem bank. Ekstraksi "
                    f"otomatis saat ini hanya mendukung PDF rekening koran yang "
                    f"diunduh langsung dari internet banking/e-statement resmi "
                    f"(bukan hasil scan atau foto kamera). Silakan unduh ulang PDF "
                    f"aslinya, atau hubungi bank untuk mendapatkan e-statement.")

            try:
                extractor = ExtractorClass(path)
            except NotImplementedError as e:
                # Format terdeteksi tapi extractor-nya memang belum ada. Ini
                # kondisi yang WAJAR, bukan kerusakan — sampaikan apa adanya.
                raise GagalEkstraksi(f"File '{nama_asli}': {e}") from e

            if extractor_pertama is None:
                extractor_pertama = extractor

            # Checksum dikumpulkan per file — kalau hanya extractor terakhir
            # yang diperiksa, file lain lolos tanpa validasi sama sekali.
            if hasattr(extractor, 'validate'):
                laporan_checksum.append((nama_asli, extractor.validate()))

            saldo = extractor.extract_saldo()
            # Cek keberadaan data BULAN, bukan sekadar dict tidak kosong.
            # Extractor mengembalikan metadata ('_nama_pemilik', dst) walau
            # tidak satu pun transaksi terbaca, sehingga `if not saldo` selalu
            # lolos dan kegagalannya baru meledak jauh di hilir.
            if not any(not k.startswith('_') for k in saldo):
                raise GagalEkstraksi(
                    f"Tidak ada satu pun periode transaksi yang bisa dibaca dari "
                    f"'{nama_asli}'. PDF-nya terbaca, tapi tata letaknya tidak "
                    f"dikenali oleh extractor {bank_code.upper()} — kemungkinan "
                    f"format/varian yang belum didukung. Pastikan file ini memang "
                    f"rekening koran {bank_code.upper()} yang diunduh langsung dari "
                    f"layanan resmi banknya.")

            per_file_results.append((nama_asli, saldo, extractor.extract_transaksi()))

        try:
            saldo_per_bulan, transaksi_per_bulan = merge_extractions(per_file_results)
        except MergeValidationError as e:
            raise GagalEkstraksi(str(e)) from e

        no_rekening = saldo_per_bulan.get('_no_rekening') or 'unknown'
        prefix = extractor_pertama.get_file_prefix()
        nama_file = f'{prefix}_{no_rekening}.xlsx'

        # Checksum internal extractor: cocokkan hasil parsing dengan angka
        # resmi yang tercetak di PDF. Hanya nama pemeriksaan & jumlah baris
        # yang dicatat — nominal & isi transaksi sengaja tidak ikut di-log.
        for nama_sumber, laporan in laporan_checksum:
            if not laporan.get('ok', True):
                gagal = sorted({
                    k for per in laporan.get('periods', [])
                    for k, v in per.get('checks', {}).items() if v is False
                })
                pesan = (f'Checksum {prefix} / {nama_sumber} TIDAK COCOK dengan '
                         f'ringkasan resmi PDF (pemeriksaan gagal: '
                         f'{", ".join(gagal) or "tidak diketahui"}). Angka pada '
                         f'laporan perlu diperiksa manual.')
                log.warning(pesan)
                peringatan.append(pesan)

            # Periode yang tumpang tindih TIDAK menggagalkan checksum, jadi
            # tanpa baris ini penggabungan duplikat terjadi tanpa diketahui
            # siapa pun — persis yang harus dihindari pada data sumber.
            digabung = laporan.get('duplikat_digabung') or 0
            if digabung:
                pesan = (f'{prefix} / {nama_sumber} memuat periode yang tumpang '
                         f'tindih: {digabung} transaksi ganda digabung menjadi '
                         f'satu. Total mutasi laporan lebih kecil dari penjumlahan '
                         f'mentah tiap periode — ini disengaja.')
                log.warning(pesan)
                peringatan.append(pesan)

        # Laporan dibangun di MEMORI, tidak pernah menyentuh disk. Lihat
        # catatan panjang di app.py soal kenapa.
        keluaran = io.BytesIO()
        create_excel(saldo_per_bulan, transaksi_per_bulan, keluaran,
                     bank_name=prefix, pdf_path=[p for p, _ in berkas])

        return {
            'nama_file': nama_file,
            'xlsx': keluaran.getvalue(),
            'no_rekening': no_rekening,
            'peringatan': peringatan,
        }

    finally:
        # PDF yang diunggah dibersihkan apa pun hasil akhirnya — sukses,
        # gagal validasi, maupun exception. Berlaku sama di kedua mode.
        shutil.rmtree(folder_job, ignore_errors=True)


def jalankan_job(bank_code: str, berkas: list, folder_job: str,
                 info_pengguna: dict, db_path: str) -> dict:
    """
    Pembungkus untuk worker RQ: jalankan ekstraksi lalu catat jejak auditnya.

    Jejak audit ditulis DI SINI, bukan di app.py, karena di mode antrean
    hasil akhirnya baru diketahui setelah request web-nya lama selesai.
    `info_pengguna` dan `db_path` diteruskan eksplisit sebab worker tidak
    punya konteks request maupun konfigurasi aplikasi.

    Nilai balik tidak memuat 'xlsx' pada kegagalan; pemanggil membedakannya
    lewat kunci 'ok'.
    """
    import auth

    def catat(hasil, no_rekening=None, keterangan=None):
        auth.catat_audit_langsung(
            db_path=db_path,
            pengguna_id=info_pengguna.get('id'),
            nama_pengguna=info_pengguna.get('nama_pengguna', '-'),
            cabang=info_pengguna.get('cabang', '-'),
            aksi='ekstraksi', hasil=hasil, bank=bank_code,
            jumlah_berkas=len(berkas),
            nama_berkas=', '.join(n for _, n in berkas),
            no_rekening=no_rekening, keterangan=keterangan)

    try:
        hasil = proses_ekstraksi(bank_code, berkas, folder_job)
    except GagalEkstraksi as e:
        catat('gagal', keterangan=str(e)[:300])
        return {'ok': False, 'pesan': str(e)}
    except Exception as e:
        log.exception('Ekstraksi %s gagal', bank_code)
        catat('gagal', keterangan=f'{type(e).__name__}: {e}'[:300])
        # Pesan teknisnya TIDAK diteruskan ke pemakai — isinya bisa memuat
        # potongan data dokumen. Sudah masuk log dan jejak audit.
        return {'ok': False,
                'pesan': ('Terjadi kesalahan saat memproses berkas. Kejadian ini '
                          'sudah dicatat — hubungi admin bila berulang.')}

    catat('berhasil', no_rekening=hasil['no_rekening'])
    hasil['ok'] = True
    return hasil
