"""
multi_pdf_merger.py — Gabungkan hasil ekstraksi dari beberapa PDF terpisah
(bukan satu PDF multi-bulan) jadi satu saldo_per_bulan/transaksi_per_bulan.

Sepenuhnya bank-agnostik: hanya membaca struktur standar yang dihasilkan
BaseExtractor.extract_saldo() / extract_transaksi() (lihat extractors/base.py).
Dipakai app.py saat user upload beberapa file PDF sekaligus untuk rekening
& bank yang sama (mis. 2 file @ 3 bulan = 6 bulan total).

Metadata extractor (kunci berawalan '_') diteruskan secara generik, jadi
extractor bisa menambah metadata baru — mis. '_checksum' atau '_biaya_admin'
— tanpa perlu mengubah file ini.

Validasi yang ditegakkan (menghentikan proses dengan MergeValidationError
kalau dilanggar — bukan best-effort merge yang bisa diam-diam salah):
  1. Semua file harus dari nomor rekening yang sama.
  2. Bulan yang sama tidak boleh muncul di lebih dari satu file.
  3. Total bulan gabungan tidak boleh melebihi MAX_BULAN.
"""

MAX_BULAN = 6

# Metadata identitas rekening: nilainya harus SATU untuk seluruh gabungan
# (sudah divalidasi sama antar file), jadi ditetapkan sekali di akhir dan
# tidak ikut alur penggabungan generik.
METADATA_IDENTITAS = ('_no_rekening', '_nama_pemilik', '_jenis_rekening')


def _gabung_metadata(gabungan: dict, key, val) -> None:
    """
    Gabungkan satu metadata extractor dari sebuah file ke hasil gabungan.

    Aturannya mengikuti bentuk datanya, bukan nama kuncinya, supaya metadata
    baru tidak perlu didaftarkan di sini:
      - list  → disambung (mis. '_checksum': laporan per periode dari tiap file)
      - dict  → di-update per kunci (mis. '_biaya_admin': jadwal per bulan);
                sub-dict digabung rekursif supaya bagian per-bulan dari dua
                file tidak saling menimpa seluruhnya
      - selain itu → file pertama menang (nilainya sama untuk seluruh rekening)

    File yang diproses lebih dulu menang saat terjadi bentrok kunci — konsisten
    dengan aturan bulan yang tidak boleh tumpang tindih antar file.
    """
    lama = gabungan.get(key)
    if lama is None:
        gabungan[key] = val
        return
    if isinstance(lama, list) and isinstance(val, list):
        gabungan[key] = lama + val
    elif isinstance(lama, dict) and isinstance(val, dict):
        hasil = dict(lama)
        for k, v in val.items():
            if isinstance(hasil.get(k), dict) and isinstance(v, dict):
                hasil[k] = {**v, **hasil[k]}
            else:
                hasil.setdefault(k, v)
        gabungan[key] = hasil
    # Skalar: biarkan nilai file pertama.


class MergeValidationError(Exception):
    """Dilempar ketika kombinasi PDF yang diupload tidak valid untuk digabung.
    Pesannya sudah dalam Bahasa Indonesia dan siap ditampilkan ke user."""
    pass


def merge_extractions(per_file_results: list) -> tuple:
    """
    Args:
        per_file_results: list of (filename, saldo_per_bulan, transaksi_per_bulan)
                           — satu tuple per file, urut sesuai urutan upload.

    Returns:
        (saldo_per_bulan_gabungan, transaksi_per_bulan_gabungan)

    Raises:
        MergeValidationError kalau rekening beda, bulan bentrok, atau total
        bulan > MAX_BULAN.
    """
    saldo_gabungan = {}
    transaksi_gabungan = {}
    no_rekening_ref = None
    nama_pemilik_ref = None
    jenis_rekening_ref = None
    sumber_rekening_ref = None
    sumber_bulan = {}  # bulan -> nama file yang pertama mengklaimnya

    for filename, saldo, transaksi in per_file_results:
        no_rek = saldo.get('_no_rekening', 'unknown')
        if no_rekening_ref is None:
            no_rekening_ref = no_rek
            nama_pemilik_ref = saldo.get('_nama_pemilik', '-')
            jenis_rekening_ref = saldo.get('_jenis_rekening', '-')
            sumber_rekening_ref = filename
        elif no_rek != no_rekening_ref:
            raise MergeValidationError(
                f"Nomor rekening tidak konsisten: '{filename}' terdeteksi rekening "
                f"{no_rek}, sedangkan '{sumber_rekening_ref}' terdeteksi rekening "
                f"{no_rekening_ref}. Pastikan semua PDF yang diupload berasal dari "
                f"rekening yang sama."
            )

        for bulan, info in saldo.items():
            if bulan.startswith('_'):
                continue
            if bulan in saldo_gabungan:
                raise MergeValidationError(
                    f"Bulan '{bulan}' ditemukan di lebih dari satu file — "
                    f"'{sumber_bulan[bulan]}' dan '{filename}'. Pastikan periode di "
                    f"antara PDF yang diupload tidak tumpang tindih."
                )
            saldo_gabungan[bulan] = info
            sumber_bulan[bulan] = filename

        # Metadata extractor diteruskan APA ADANYA, tanpa daftar putih.
        #
        # Sebelumnya hanya '_saldo_awal_*' yang diteruskan, sehingga metadata
        # lain yang dikirim extractor (mis. '_checksum' hasil pencocokan dengan
        # ringkasan resmi PDF) diam-diam terbuang di sini — padahal app.py
        # SELALU memanggil merger, bahkan untuk satu file. Akibatnya
        # pemeriksaan yang bergantung metadata itu tidak pernah menyala sama
        # sekali. Meneruskan secara generik membuat extractor bisa menambah
        # metadata baru tanpa perlu menyentuh file ini.
        for key, val in saldo.items():
            if not key.startswith('_') or key in METADATA_IDENTITAS:
                continue
            _gabung_metadata(saldo_gabungan, key, val)

        for bulan, df in transaksi.items():
            # Bulan yang sama sudah divalidasi lewat saldo di atas — dict transaksi
            # hanya berisi bulan yang benar-benar punya transaksi, jadi cukup timpa.
            transaksi_gabungan[bulan] = df

    jumlah_bulan = len([b for b in saldo_gabungan if not b.startswith('_')])
    if jumlah_bulan > MAX_BULAN:
        raise MergeValidationError(
            f"Total {jumlah_bulan} bulan dari {len(per_file_results)} file melebihi "
            f"batas maksimum {MAX_BULAN} bulan. Kurangi jumlah file PDF yang diupload."
        )

    saldo_gabungan['_no_rekening'] = no_rekening_ref
    saldo_gabungan['_nama_pemilik'] = nama_pemilik_ref
    saldo_gabungan['_jenis_rekening'] = jenis_rekening_ref

    return saldo_gabungan, transaksi_gabungan
