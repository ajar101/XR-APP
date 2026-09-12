"""
bni_nama.py — Pipeline nama lawan transaksi untuk dokumen BNI.

Dipakai bersama oleh extractor BNI ACCOUNT STATEMENT (bni_statement.py) dan
TRANSACTION INQUIRY (bni_inquiry.py). Kedua format itu berbeda tata letak,
tapi MENCETAK KOLOM KETERANGAN DENGAN TATA BAHASA YANG SAMA — segmen dipisah
'|', klausa "PEMINDAHAN KE <rekening> <nama>", kode cabang 3 digit di depan
nama pengirim antarbank, dan seterusnya. Aturan pembacaannya karena itu
tinggal satu tempat: kalau disalin, perbaikan pada satu format diam-diam
tidak ikut berlaku di format lain.

Bentuknya mixin, bukan fungsi lepas, supaya extractor bisa menimpa satu
konstanta saja (mis. LEBAR_NAMA_KREDIT) tanpa menyalin seluruh pipeline.
Setara dengan extractors/mandiri_nama.py untuk keluarga dokumen Mandiri.
"""

import re


class NamaLawanBNI:
    """Pembaca nama pihak lawan dari kolom keterangan dokumen BNI."""

    # Baris yang dibukukan bank sendiri: tidak ada lawan transaksi, tapi
    # perlu label tetap supaya terkelompok rapi di Rekap Debit/Kredit.
    # Dicocokkan pada kepala keterangan (segmen pertama), persis sama.
    LABEL_BANK = {
        'BIAYA ADM REK':     'Biaya Admin',
        'BY TRX ATM PRIMA':  'Biaya Transaksi ATM Prima',
        'BY TRX BIFAST':     'Biaya Transaksi BI-FAST',
        'JASA GIRO/BUNGA':   'Jasa Giro/Bunga',
        'PPH':               'PPh Jasa Giro',
    }
    # Penarikan warkat: nomor di belakangnya adalah nomor warkat, bukan nama.
    LABEL_WARKAT = (
        ('TARIK CHQ/BG',  'Tarik Cek/Bilyet Giro'),
        ('TARIK CHQ',     'Tarik Cek'),
        ('TARIK KLIRING', 'Tarik Kliring'),
        ('TARIK TUNAI',   'Tarik Tunai'),
        ('SETOR TUNAI',   'Setor Tunai'),
    )

    # "PEMINDAHAN KE 1815273089 CV RANGGA JAYA TRANS"
    RE_PEMINDAHAN = re.compile(r'PEMINDAHAN\s+(?:KE|DARI)\s+(\d+)\s*(.*)$')
    # "535 NUSANTARA EKSPR 23 ACR Invoice ..." — kode cabang 3 digit di depan.
    RE_KODE_CABANG = re.compile(r'^(\d{3})\s+(.*)$')
    # "HA BONGSUNG -PT BANK WOORI SAU" — nama, lalu bank asal setelah " -".
    RE_NAMA_BANK = re.compile(r'^(.*?)\s+-\s*(?:PT\s+)?BANK\b')
    # Label kanal, bukan nama pihak.
    LABEL_KANAL = {'BNI DIRECT', 'BNI DIRECT.', 'BNIDIRECT'}
    # Sapaan yang lazim mendahului nama orang dan ikut dicetak bank.
    SAPAAN = {'BPK', 'BP', 'IBU', 'IBU.', 'BPK.', 'SDR', 'SDRI', 'TN', 'NY', 'HJ'}
    # Lebar kolom nama pada baris kredit antarbank: 15 karakter, sisanya
    # berita transaksi yang menempel tanpa pemisah (lihat _potong_lebar).
    LEBAR_NAMA_KREDIT = 15
    # Kata yang mencampur huruf dan angka dalam satu kata ("S1ACIR9510",
    # "BWS0"): penanda kode mesin, bukan nama pihak (lihat _terbaca_sebagai_nama).
    RE_KATA_KODE = re.compile(r'^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]+$')
    # Nama kanal transfer yang dicetak MENEMPEL di ekor nama pengirim
    # ("PT. POS LOGISTIK IND BI FAST Transfer"). Kanalnya bukan bagian nama,
    # dan kalau dibiarkan, pengirim yang sama terpecah antara baris yang
    # berekor kanal dan yang tidak. Daftarnya sengaja hanya memuat label yang
    # benar-benar terlihat di PDF referensi — menambah label yang belum
    # pernah muncul berisiko memotong nama pihak yang kebetulan mirip.
    RE_EKOR_KANAL = re.compile(r'\s+BI[\s-]?FAST\s*$', re.IGNORECASE)

    def _nama_lawan(self, keterangan: str, arah: str) -> str:
        """
        Nama pihak lawan transaksi dari kolom Transaction Description.

        Kolom ini tersusun atas beberapa segmen dipisah '|'. Yang bisa
        dipastikan hanya bentuk-bentuk di bawah; selebihnya dikembalikan
        nomor rekening lawan yang tercetak — itu identitas yang memang
        disediakan dokumen, bukan tebakan, dan tetap mengelompokkan
        transaksi ke pihak yang sama di Rekap.
        """
        segmen = [s.strip() for s in (keterangan or '').split('|') if s.strip()]
        if not segmen:
            return ''

        kepala = segmen[0]
        # "KOR ..." = koreksi atas transaksi sejenis; polanya sama.
        if kepala.upper().startswith('KOR '):
            kepala = kepala[4:].strip()

        if kepala.upper() in self.LABEL_BANK:
            return self.LABEL_BANK[kepala.upper()]
        for awalan, label in self.LABEL_WARKAT:
            if kepala.upper().startswith(awalan):
                # "SETOR TUNAI | DAENG AJAM NURJAMIL | <berita>"
                if len(segmen) > 1 and not segmen[1][:1].isdigit():
                    return self._rapikan(segmen[1])
                return label

        # "TRANSFER KE | PEMINDAHAN KE 327655583 DAPENSI DWIKARYA | ..."
        for i, seg in enumerate(segmen):
            if i == 0:
                continue
            m = self.RE_PEMINDAHAN.match(seg)
            if not m:
                continue
            rekening, ekor = m.group(1), m.group(2).strip()
            nama = self._rapikan(self._potong_berita(ekor))
            if self._terbaca_sebagai_nama(nama):
                return nama
            # Nama tidak menempel di klausa pemindahan. Segmen terakhir masih
            # bisa memuatnya: pada transaksi masuk lewat e-channel segmen itu
            # berisi "<NAMA PENGIRIM> <berita>".
            #
            # Kecuali kalau segmen itu dibuka nomor rekening lawan yang tadi
            # juga: bentuk itu adalah nomor referensi diikuti berita
            # ("3819622222 Pelunasan KIR mobil tangki"), tidak pernah memuat
            # nama, dan menambangnya hanya menghasilkan potongan berita yang
            # menyamar jadi nama pihak.
            akhir = segmen[-1]
            if (i != len(segmen) - 1
                    and akhir.upper() not in self.LABEL_KANAL
                    and not re.match(r'^0*' + rekening + r'\b', akhir)):
                nama = self._rapikan(self._potong_berita(akhir))
                if self._terbaca_sebagai_nama(nama):
                    return nama
            # Dokumen tidak mencetak nama pihak lawan untuk transaksi ini —
            # yang tersedia hanya nomor rekeningnya. Nomor itu yang dipakai:
            # bukan tebakan, dan tetap menyatukan transaksi ke pihak yang
            # sama saat direkap.
            return rekening

        # "KREDIT LAIN-LAIN | 535 NUSANTARA EKSPR 23 ACR Invoice ..."
        m = self.RE_KODE_CABANG.match(segmen[1]) if len(segmen) > 1 else None
        if m:
            return self._rapikan(self._potong_lebar(m.group(2), self.LEBAR_NAMA_KREDIT))

        # "TRANSFER DARI | HA BONGSUNG -PT BANK WOORI SAU | ..."
        if len(segmen) > 1:
            m = self.RE_NAMA_BANK.match(segmen[1])
            if m:
                return self._rapikan(m.group(1))
            if segmen[1].upper() not in self.LABEL_KANAL:
                return self._rapikan(segmen[1])
        return ''

    @staticmethod
    def _potong_lebar(teks: str, lebar: int) -> str:
        """
        Ambil kata-kata pertama yang masih muat dalam kolom selebar `lebar`.

        Dipakai untuk baris kredit antarbank, yang mencetak nama pengirim
        pada kolom tetap 15 karakter lalu menyambungnya langsung dengan
        berita transaksi tanpa pemisah apa pun ("NUSANTARA EKSPR 23 ACR
        Invoice ..."). Batas kolomnya yang jadi pemisah, bukan tanda baca.
        """
        semua = teks.split()
        hasil = []
        panjang = 0
        for kata in semua:
            tambah = len(kata) + (1 if hasil else 0)
            if panjang + tambah > lebar:
                break
            hasil.append(kata)
            panjang += tambah
        # Kata pertama yang sendirian sudah melebihi lebar kolom tetap
        # dipakai: memotongnya di tengah kata justru merusak namanya.
        if not hasil:
            return semua[0] if semua else ''
        return ' '.join(hasil)

    @classmethod
    def _terbaca_sebagai_nama(cls, teks: str) -> bool:
        """
        Apakah teks ini benar-benar nama pihak, bukan kode mesin?

        Kolom keterangan e-channel BNI kerap diisi kode terminal/agen dan
        nomor urutnya alih-alih nama pengirim: "S1ACIR9510 4095",
        "62800200 BWS0", "99102000 7377". Kode seperti itu berganti tiap
        transaksi, jadi kalau diperlakukan sebagai nama, satu pengirim yang
        sama pecah jadi puluhan baris di Rekap — persis informasi yang mau
        dirangkum sheet itu.

        Dua tanda yang membedakannya, keduanya harus terpenuhi:
          - tidak ada kata yang mencampur huruf dan angka di dalam satu kata
            ("S1ACIR9510", "BWS0"); nama orang & badan usaha tidak begitu;
          - ada setidaknya satu kata yang murni huruf dan panjangnya ≥2,
            sehingga "99102000 7377" yang seluruhnya angka ikut tersaring.

        Yang tidak lolos BUKAN dibuang: pemanggilnya jatuh ke nomor rekening
        lawan yang tercetak di baris yang sama — identitas yang tetap dari
        transaksi ke transaksi, jadi transaksinya terkumpul jadi satu.
        """
        kata = [k.strip('.,-/()') for k in (teks or '').split()]
        kata = [k for k in kata if k]
        if not kata:
            return False
        if any(cls.RE_KATA_KODE.match(k) for k in kata):
            return False
        return any(len(k) >= 2 and k.isalpha() for k in kata)

    @classmethod
    def _potong_berita(cls, ekor: str) -> str:
        """
        Pisahkan nama dari berita transaksi yang menempel di belakangnya.

        Bank mencetak keduanya berurutan tanpa pemisah apa pun
        ("ALI SYAMSUDI STABIL an Akhmad Ridwan"), jadi yang bisa dipakai
        hanya bentuk hurufnya: nama pihak dicetak sistem dalam HURUF BESAR
        semua, sedangkan berita diketik nasabah sendiri sehingga hampir
        selalu mengandung huruf kecil. Kata pertama yang mengandung huruf
        kecil karena itu menandai awal berita.

        Kalau justru kata pertamanya sudah berhuruf kecil, berarti tidak ada
        nama sama sekali di situ — seluruhnya berita ("1730018931288
        pemindahbukuan ke mtf"), dan yang dikembalikan kosong.
        """
        kata = (ekor or '').split()
        hasil = []
        for i, k in enumerate(kata):
            bersapa = k.upper().strip('.,') in cls.SAPAAN
            if i and any(c.islower() for c in k) and not bersapa:
                break
            if not i and k.islower():
                return ''
            hasil.append(k)
        nama = ' '.join(hasil)
        return '' if nama.replace('.', '').isdigit() else nama

    @staticmethod
    def _rapikan(nama: str) -> str:
        """Buang nomor referensi & tanda baca yang menempel di ekor nama."""
        nama = ' '.join((nama or '').split())
        nama = re.sub(r'\s+TRF\s+TO:\S*.*$', '', nama, flags=re.IGNORECASE)
        nama = re.sub(r'\s+NO\s*:\S*.*$', '', nama, flags=re.IGNORECASE)
        nama = re.sub(r'(?:\s+\d{6,})+\s*$', '', nama)
        # "BILL PAYMENT (MPN G2 IDR )" -> "BILL PAYMENT (MPN G2 IDR)"
        nama = re.sub(r'\s+([)\]])', r'\1', nama)
        nama = NamaLawanBNI.RE_EKOR_KANAL.sub('', nama)
        return nama.strip(' .,-/|')

