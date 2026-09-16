"""
wsgi.py — Titik masuk untuk server WSGI produksi (gunicorn/uWSGI).

    gunicorn -c gunicorn.conf.py wsgi:app

Kenapa ada berkas terpisah dan bukan langsung `gunicorn app:app`: blok
`if __name__ == '__main__'` di app.py hanya untuk menjalankan aplikasi di
mesin sendiri saat mengembangkan, dan server bawaan Werkzeug di sana bukan
server produksi. Memberi produksi pintu masuknya sendiri membuat pemisahan
itu terlihat, bukan cuma disepakati.
"""

from app import app

__all__ = ['app']
