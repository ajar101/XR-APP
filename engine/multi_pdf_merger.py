"""
multi_pdf_merger.py — Gabungkan hasil ekstraksi dari beberapa PDF terpisah
(bukan satu PDF multi-bulan) jadi satu saldo_per_bulan/transaksi_per_bulan.

Sepenuhnya bank-agnostik: hanya membaca struktur standar yang dihasilkan
BaseExtractor.extract_saldo() / extract_transaksi() (lihat extractors/base.py).
Dipakai app.py saat user upload beberapa file PDF sekaligus untuk rekening
& bank yang sama (mis. 2 file @ 3 bulan = 6 bulan total).

Validasi yang ditegakkan (menghentikan proses dengan MergeValidationError
kalau dilanggar — bukan best-effort merge yang bisa diam-diam salah):
  1. Semua file harus dari nomor rekening yang sama.
  2. Bulan yang sama tidak boleh muncul di lebih dari satu file.
  3. Total bulan gabungan tidak boleh melebihi MAX_BULAN.
"""

MAX_BULAN = 6


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

        for key, val in saldo.items():
            if key.startswith('_saldo_awal_'):
                saldo_gabungan[key] = val

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
