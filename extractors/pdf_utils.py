"""
pdf_utils.py — Utility bank-agnostik seputar validasi PDF sebelum diekstrak.

Dipakai app.py di awal alur upload, SEBELUM memanggil extractor bank
manapun, supaya kegagalan yang sifatnya "PDF-nya sendiri tidak bisa
diproses" (mis. hasil scan/foto tanpa teks) langsung ketahuan dengan
pesan jelas — bukan menunggu extractor jalan penuh (bisa puluhan detik
untuk PDF ratusan halaman) lalu gagal dengan pesan generik.
"""

import pdfplumber


def is_probably_scanned(pdf_path: str, sample_pages: int = 5, min_chars: int = 30) -> bool:
    """
    Deteksi heuristik: PDF ini kemungkinan hasil scan/foto (gambar raster),
    bukan PDF teks asli dari sistem core banking.

    pdfplumber.extract_text() hanya membaca objek teks yang tertanam di
    PDF — untuk PDF hasil scan/foto kamera, halamannya murni gambar
    raster tanpa layer teks sama sekali, jadi extract_text() akan selalu
    kembali kosong walau halamannya penuh tulisan (yang sebenarnya cuma
    piksel gambar).

    Heuristiknya: ambil sampel beberapa halaman pertama — kalau SEMUA
    sampel nyaris tanpa teks (di bawah `min_chars`) NAMUN memang berisi
    gambar (bukan halaman kosong beneran), maka PDF ini didiagnosis
    hasil scan. Mengecek beberapa halaman (bukan cuma satu) supaya tidak
    salah tangkap PDF teks asli yang kebetulan punya satu halaman sampul
    dengan sedikit teks.

    Catatan: ini deteksi, BUKAN dukungan ekstraksi. PDF hasil scan tetap
    tidak bisa diekstrak otomatis oleh extractor berbasis teks (bca.py,
    dst) — butuh OCR atau vision model terpisah yang belum diimplementasikan.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return False
            pages = pdf.pages[:sample_pages]

            semua_nyaris_kosong = True
            ada_gambar = False
            for page in pages:
                text = (page.extract_text() or '').strip()
                if len(text) >= min_chars:
                    semua_nyaris_kosong = False
                if page.images:
                    ada_gambar = True

            return semua_nyaris_kosong and ada_gambar
    except Exception:
        # Kalau PDF-nya sendiri tidak bisa dibuka (corrupt, dsb), biarkan
        # alur ekstraksi normal yang melaporkan errornya — jangan salah
        # diagnosis di sini.
        return False
