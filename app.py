"""
app.py — XR-App Flask entrypoint.

Bertanggung jawab hanya untuk:
  1. Menyajikan UI (halaman muka dengan pilihan bank)
  2. Menerima upload PDF
  3. Memanggil extractor yang tepat berdasarkan pilihan bank
  4. Memanggil excel_builder untuk generate output
  5. Mengirimkan file Excel ke user

Tidak ada logika parsing PDF atau styling Excel di sini.
"""

import os
from flask import Flask, render_template_string, request, send_file, jsonify

from extractors.registry import get_enabled_banks, get_extractor
from engine.excel_builder import create_excel
from engine.multi_pdf_merger import merge_extractions, MergeValidationError

# DEBUG: Print enabled banks
print("\n" + "="*60)
print("ENABLED BANKS:")
print("="*60)
for code, info in get_enabled_banks().items():
    print(f"  ✓ {code:12s} - {info['name']}")
print("="*60 + "\n")

app = Flask(__name__)

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
EXPORT_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'exports')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EXPORT_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER']      = UPLOAD_FOLDER
app.config['EXPORT_FOLDER']      = EXPORT_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # dinaikkan dari 16MB — mendukung upload multi-PDF sekaligus

# ============================================================
# HTML TEMPLATE
# ============================================================

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>XR-App · eXtract-Report</title>
    <link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --ink:     #0D1117;
            --paper:   #F6F8FA;
            --accent:  #1A6BFF;
            --accent2: #00C896;
            --border:  #D0D7DE;
            --muted:   #656D76;
            --surface: #FFFFFF;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'DM Sans', sans-serif;
            background-color: var(--paper);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }

        /* ── Top bar ── */
        .topbar {
            display: flex; align-items: center; justify-content: space-between;
            padding: 18px 40px;
            background: var(--surface);
            border-bottom: 1px solid var(--border);
        }
        .logo { display: flex; align-items: center; gap: 10px; }
        .logo-mark {
            width: 30px; height: 30px;
            background: var(--ink); border-radius: 6px;
            display: flex; align-items: center; justify-content: center;
        }
        .logo-text {
            font-family: 'DM Mono', monospace;
            font-size: 15px; font-weight: 500; letter-spacing: -0.3px; color: var(--ink);
        }
        .logo-text span { color: var(--accent); }
        .version-badge {
            font-family: 'DM Mono', monospace; font-size: 11px;
            color: var(--muted); background: var(--paper);
            border: 1px solid var(--border); padding: 3px 8px; border-radius: 20px;
        }

        /* ── Main layout ── */
        .main {
            flex: 1;
            display: grid;
            grid-template-columns: 1fr 460px 1fr;
            min-height: calc(100vh - 65px);
        }
        .left-panel {
            padding: 60px 40px 60px 60px;
            display: flex; flex-direction: column; justify-content: center;
            border-right: 1px solid var(--border);
        }
        .left-panel h2 {
            font-size: 30px; font-weight: 600; color: var(--ink);
            line-height: 1.25; letter-spacing: -0.5px; margin-bottom: 12px;
        }
        .left-panel h2 em { font-style: normal; color: var(--accent); }
        .left-panel p {
            font-size: 14px; color: var(--muted); line-height: 1.7;
            max-width: 300px; margin-bottom: 32px;
        }
        .feature-list { list-style: none; display: flex; flex-direction: column; gap: 10px; }
        .feature-list li {
            display: flex; align-items: center; gap: 10px;
            font-size: 13px; color: var(--ink);
        }
        .feature-list li::before {
            content: ''; width: 18px; height: 18px; min-width: 18px;
            background: var(--accent2); border-radius: 50%;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'%3E%3Cpath d='M2 6l3 3 5-5' stroke='white' stroke-width='1.5' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
            background-size: 12px; background-position: center; background-repeat: no-repeat;
        }

        /* ── Center panel ── */
        .center-panel {
            padding: 40px 36px;
            background: var(--surface);
            border-right: 1px solid var(--border);
            display: flex; flex-direction: column; justify-content: center;
        }
        .form-header { margin-bottom: 24px; }
        .form-header h3 {
            font-size: 18px; font-weight: 600; color: var(--ink);
            letter-spacing: -0.3px; margin-bottom: 4px;
        }
        .form-header p { font-size: 13px; color: var(--muted); }

        /* ── Bank selector ── */
        .section-label {
            font-family: 'DM Mono', monospace; font-size: 11px; font-weight: 500;
            letter-spacing: 0.5px; text-transform: uppercase;
            color: var(--muted); margin-bottom: 10px;
        }
        .bank-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
            gap: 8px;
            margin-bottom: 20px;
        }
        .bank-card {
            border: 1.5px solid var(--border); border-radius: 8px;
            padding: 14px 10px; text-align: center;
            cursor: pointer; transition: all 0.15s;
            background: var(--paper);
            position: relative;
        }
        .bank-card:hover { border-color: var(--accent); background: #F0F6FF; }
        .bank-card.selected {
            border-color: var(--accent); background: #EBF3FF;
            box-shadow: 0 0 0 3px rgba(26,107,255,0.12);
        }
        .bank-card.disabled {
            opacity: 0.4; cursor: not-allowed;
            pointer-events: none;
        }
        .bank-logo {
            width: 44px; height: 44px; border-radius: 8px;
            display: flex; align-items: center; justify-content: center;
            margin: 0 auto 8px;
            font-family: 'DM Mono', monospace; font-size: 11px; font-weight: 500;
            color: white; letter-spacing: 0.5px;
        }
        .bank-card-name { font-size: 12px; font-weight: 500; color: var(--ink); }
        .bank-card-desc { font-size: 10px; color: var(--muted); margin-top: 2px; }
        .coming-badge {
            position: absolute; top: 6px; right: 6px;
            font-size: 8px; font-weight: 600; letter-spacing: 0.3px;
            background: var(--border); color: var(--muted);
            padding: 2px 5px; border-radius: 3px; text-transform: uppercase;
        }
        .selected-badge {
            position: absolute; top: 6px; right: 6px;
            width: 14px; height: 14px;
            background: var(--accent); border-radius: 50%;
            display: none;
            align-items: center; justify-content: center;
        }
        .bank-card.selected .selected-badge { display: flex; }
        .selected-badge::after {
            content: '';
            width: 6px; height: 6px;
            background: white; border-radius: 50%;
        }

        /* ── Upload zone ── */
        .upload-zone {
            border: 1.5px dashed var(--border); border-radius: 8px;
            padding: 30px 24px; text-align: center;
            cursor: pointer; transition: border-color 0.2s, background 0.2s;
            background: var(--paper); margin-bottom: 14px;
        }
        .upload-zone:hover, .upload-zone.dragover { border-color: var(--accent); background: #F0F6FF; }
        .upload-zone.has-file { border-color: var(--accent2); background: #F0FBF7; }
        .upload-icon-wrap {
            width: 40px; height: 40px;
            background: var(--surface); border: 1.5px solid var(--border); border-radius: 8px;
            display: flex; align-items: center; justify-content: center; margin: 0 auto 12px;
        }
        .upload-zone.has-file .upload-icon-wrap { border-color: var(--accent2); background: #E8F8F2; }
        .upload-label { font-size: 13px; font-weight: 500; color: var(--ink); margin-bottom: 4px; }
        .upload-hint  { font-size: 12px; color: var(--muted); }
        .file-selected {
            font-family: 'DM Mono', monospace; font-size: 12px;
            color: var(--accent2); margin-top: 8px; word-break: break-all;
        }
        input[type="file"] { display: none; }

        /* ── Button ── */
        .btn-primary {
            width: 100%; padding: 13px;
            background: var(--ink); color: white; border: none; border-radius: 6px;
            font-family: 'DM Sans', sans-serif; font-size: 14px; font-weight: 600;
            cursor: pointer; transition: background 0.15s, transform 0.1s;
            display: flex; align-items: center; justify-content: center; gap: 8px;
        }
        .btn-primary:hover   { background: #1c2128; }
        .btn-primary:active  { transform: scale(0.99); }
        .btn-primary:disabled { background: var(--border); color: var(--muted); cursor: not-allowed; transform: none; }

        /* ── Loading & Error ── */
        .loading-state { display: none; text-align: center; padding: 16px 0 4px; }
        .progress-bar  { width: 100%; height: 3px; background: var(--border); border-radius: 2px; overflow: hidden; margin-bottom: 10px; }
        .progress-fill { height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent2)); border-radius: 2px; animation: progress 2s ease-in-out infinite; }
        @keyframes progress {
            0%   { width: 0%;  margin-left: 0; }
            50%  { width: 60%; margin-left: 20%; }
            100% { width: 0%;  margin-left: 100%; }
        }
        .loading-text { font-size: 12px; color: var(--muted); font-family: 'DM Mono', monospace; }
        .error-box {
            background: #FFF0F0; border: 1px solid #FFCDD2; color: #C62828;
            padding: 10px 14px; border-radius: 6px; font-size: 13px;
            margin-top: 10px; display: none;
        }

        /* ── Right panel ── */
        .right-panel {
            padding: 60px 40px;
            display: flex; flex-direction: column; justify-content: center;
        }
        .output-label {
            font-family: 'DM Mono', monospace; font-size: 11px; font-weight: 500;
            letter-spacing: 0.5px; text-transform: uppercase;
            color: var(--muted); margin-bottom: 16px;
        }
        .sheet-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 36px; }
        .sheet-item {
            display: flex; align-items: center; gap: 10px;
            padding: 10px 12px; border: 1px solid var(--border);
            border-radius: 6px; background: var(--surface); font-size: 13px;
        }
        .sheet-num { font-family: 'DM Mono', monospace; font-size: 11px; color: var(--muted); min-width: 20px; }
        .sheet-name { font-weight: 500; color: var(--ink); flex: 1; }
        .sheet-desc { font-size: 11px; color: var(--muted); }
        .footer-note {
            font-size: 12px; color: var(--muted); line-height: 1.6;
            border-top: 1px solid var(--border); padding-top: 16px;
        }
        .footer-note strong { color: var(--ink); font-weight: 500; }

        /* ── Bottom bar ── */
        .bottombar {
            padding: 14px 40px; background: var(--surface);
            border-top: 1px solid var(--border);
            display: flex; align-items: center; justify-content: space-between;
        }
        .bottombar-left  { font-family: 'DM Mono', monospace; font-size: 11px; color: var(--muted); }
        .bottombar-right { font-size: 11px; color: var(--muted); }

        @media (max-width: 900px) {
            .main { grid-template-columns: 1fr; }
            .left-panel, .right-panel { display: none; }
            .center-panel { border: none; min-height: calc(100vh - 120px); }
            .topbar { padding: 16px 24px; }
        }
    </style>
</head>
<body>

<header class="topbar">
    <div class="logo">
        <div class="logo-mark">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <rect x="2" y="10" width="3" height="4" rx="0.5" fill="#1A6BFF"/>
                <rect x="6.5" y="6" width="3" height="8" rx="0.5" fill="#00C896"/>
                <rect x="11" y="2" width="3" height="12" rx="0.5" fill="white"/>
            </svg>
        </div>
        <span class="logo-text">XR<span>-App</span></span>
    </div>
    <span class="version-badge">v2.0 · Multi-Bank</span>
</header>

<main class="main">

    <!-- Left panel -->
    <section class="left-panel">
        <h2>Analisis Rekening Koran <em>Otomatis</em></h2>
        <p>Upload PDF rekening koran, pilih bank, dan dapatkan laporan Excel lengkap dalam hitungan detik.</p>
        <ul class="feature-list">
            <li>Ekstraksi saldo harian & transaksi</li>
            <li>Rekap kredit & debit per pengirim</li>
            <li>Cashflow harian per bulan</li>
            <li>Kategorisasi transaksi otomatis</li>
            <li>Analisis konsentrasi & HHI Score</li>
            <li>Summary keuangan multi-bulan</li>
        </ul>
    </section>

    <!-- Center panel — form -->
    <section class="center-panel">
        <div class="form-header">
            <h3>Ekstrak Rekening Koran</h3>
            <p>Pilih bank, lalu upload file PDF rekening koran.</p>
        </div>

        <!-- Pilih Bank -->
        <div class="section-label">1. Pilih Bank</div>
        <div class="bank-grid" id="bankGrid">
            {% for code, bank in banks.items() %}
            <div class="bank-card {% if not bank.enabled %}disabled{% endif %}"
                 data-code="{{ code }}"
                 onclick="selectBank(this, '{{ code }}', '{{ bank.name }}')"
                 title="{{ bank.description }}">
                <div class="bank-logo" style="background: {{ bank.color }}">{{ bank.logo_text }}</div>
                <div class="bank-card-name">{{ bank.short_name }}</div>
                <div class="bank-card-desc">{{ bank.description }}</div>
                {% if not bank.enabled %}
                <span class="coming-badge">Soon</span>
                {% endif %}
                <span class="selected-badge"></span>
            </div>
            {% endfor %}
        </div>

        <!-- Upload PDF -->
        <div class="section-label">2. Upload PDF Rekening Koran</div>
        <form id="uploadForm" action="/upload" method="post" enctype="multipart/form-data">
            <input type="hidden" name="bank_code" id="bankCodeInput" value="">

            <div class="upload-zone" id="uploadZone"
                 onclick="document.getElementById('fileInput').click()"
                 ondragover="handleDragOver(event)"
                 ondragleave="handleDragLeave(event)"
                 ondrop="handleDrop(event)">
                <div class="upload-icon-wrap">
                    <!-- PDF icon -->
                    <svg id="iconPdf" width="20" height="20" viewBox="0 0 20 20" fill="none">
                        <rect x="3" y="1" width="11" height="15" rx="1.5" stroke="#656D76" stroke-width="1.5"/>
                        <path d="M11 1v5h5" stroke="#656D76" stroke-width="1.5" stroke-linecap="round"/>
                        <path d="M6 10h8M6 13h5" stroke="#656D76" stroke-width="1.2" stroke-linecap="round"/>
                    </svg>
                    <!-- Check icon -->
                    <svg id="iconCheck" width="20" height="20" viewBox="0 0 20 20" fill="none" style="display:none">
                        <circle cx="10" cy="10" r="8" stroke="#00C896" stroke-width="1.5"/>
                        <path d="M6.5 10l2.5 2.5 4.5-4.5" stroke="#00C896" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                </div>
                <div class="upload-label" id="uploadLabel">Klik atau drop file di sini</div>
                <div class="upload-hint"  id="uploadHint">Bisa pilih lebih dari 1 PDF · Maks 6 bulan mutasi total · Maks 64 MB</div>
                <div class="file-selected" id="fileName"></div>
            </div>

            <input type="file" id="fileInput" name="file" accept=".pdf" multiple>

            <div id="loadingState" class="loading-state">
                <div class="progress-bar"><div class="progress-fill"></div></div>
                <div class="loading-text" id="loadingText">Memproses...</div>
            </div>

            <div id="errorBox" class="error-box"></div>

            <button type="submit" class="btn-primary" id="submitBtn" disabled>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <path d="M8 2v9M4 7l4 4 4-4" stroke="white" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                    <path d="M2 13h12" stroke="white" stroke-width="1.5" stroke-linecap="round"/>
                </svg>
                Ekstrak &amp; Unduh Excel
            </button>
        </form>
    </section>

    <!-- Right panel -->
    <section class="right-panel">
        <div class="output-label">Output · 8 Sheet Excel</div>
        <div class="sheet-list">
            <div class="sheet-item"><span class="sheet-num">01</span><span class="sheet-name">Saldo Harian</span><span class="sheet-desc">Per tanggal</span></div>
            <div class="sheet-item"><span class="sheet-num">02</span><span class="sheet-name">Detail Transaksi</span><span class="sheet-desc">Semua mutasi</span></div>
            <div class="sheet-item"><span class="sheet-num">03</span><span class="sheet-name">Rekap Kredit</span><span class="sheet-desc">Per pengirim</span></div>
            <div class="sheet-item"><span class="sheet-num">04</span><span class="sheet-name">Rekap Debit</span><span class="sheet-desc">Per penerima</span></div>
            <div class="sheet-item"><span class="sheet-num">05</span><span class="sheet-name">Cashflow Harian</span><span class="sheet-desc">Net per hari</span></div>
            <div class="sheet-item"><span class="sheet-num">06</span><span class="sheet-name">Kategori Debit</span><span class="sheet-desc">Auto-klasifikasi</span></div>
            <div class="sheet-item"><span class="sheet-num">07</span><span class="sheet-name">Kategori Kredit</span><span class="sheet-desc">Auto-klasifikasi</span></div>
            <div class="sheet-item"><span class="sheet-num">08</span><span class="sheet-name">Summary + HHI</span><span class="sheet-desc">Konsentrasi</span></div>
        </div>
        <div class="footer-note">
            <strong>Catatan:</strong> Hasil analisis otomatis. Selalu lakukan cross-check dengan dokumen asli sebelum digunakan untuk keputusan penting.
        </div>
    </section>

</main>

<footer class="bottombar">
    <span class="bottombar-left">XR-App · eXtract-Report v2.0 · © 2026 Ajar D Ashaq</span>
    <span class="bottombar-right" id="footerBankInfo">Pilih bank untuk memulai</span>
</footer>

<script>
    let selectedBank = null;

    const fileInput   = document.getElementById('fileInput');
    const fileName    = document.getElementById('fileName');
    const form        = document.getElementById('uploadForm');
    const loadingState= document.getElementById('loadingState');
    const loadingText = document.getElementById('loadingText');
    const submitBtn   = document.getElementById('submitBtn');
    const errorBox    = document.getElementById('errorBox');
    const uploadZone  = document.getElementById('uploadZone');
    const uploadLabel = document.getElementById('uploadLabel');
    const uploadHint  = document.getElementById('uploadHint');
    const iconPdf     = document.getElementById('iconPdf');
    const iconCheck   = document.getElementById('iconCheck');
    const bankCodeInput = document.getElementById('bankCodeInput');
    const footerBankInfo = document.getElementById('footerBankInfo');

    function selectBank(el, code, name) {
        document.querySelectorAll('.bank-card').forEach(c => c.classList.remove('selected'));
        el.classList.add('selected');
        selectedBank = code;
        bankCodeInput.value = code;
        footerBankInfo.textContent = name;
        checkReady();
    }

    function checkReady() {
        const hasFile = fileInput.files && fileInput.files.length > 0;
        submitBtn.disabled = !(selectedBank && hasFile);
    }

    fileInput.addEventListener('change', function(e) {
        if (e.target.files.length > 0) {
            setFilesSelected(e.target.files);
        }
    });

    function setFilesSelected(files) {
        const names = Array.from(files).map(f => f.name);
        fileName.textContent    = names.join(', ');
        uploadLabel.textContent = files.length > 1
            ? `${files.length} file terpilih`
            : 'File terpilih';
        uploadHint.textContent  = 'Klik untuk ganti file';
        uploadZone.classList.add('has-file');
        iconPdf.style.display   = 'none';
        iconCheck.style.display = 'block';
        checkReady();
    }

    function handleDragOver(e) { e.preventDefault(); uploadZone.classList.add('dragover'); }
    function handleDragLeave(e) { uploadZone.classList.remove('dragover'); }
    function handleDrop(e) {
        e.preventDefault();
        uploadZone.classList.remove('dragover');
        const files = Array.from(e.dataTransfer.files);
        if (files.length > 0 && files.every(f => f.name.toLowerCase().endsWith('.pdf'))) {
            fileInput.files = e.dataTransfer.files;
            setFilesSelected(e.dataTransfer.files);
        } else {
            showError('Hanya file PDF yang didukung.');
        }
    }

    function showError(msg) {
        errorBox.textContent   = msg;
        errorBox.style.display = 'block';
    }

    const loadingSteps = [
        'Membaca PDF...', 'Mengekstrak transaksi...', 'Menyusun pivot table...',
        'Menghitung HHI score...', 'Menulis Excel...'
    ];
    let stepIdx = 0, stepTimer = null;

    form.addEventListener('submit', async function(e) {
        e.preventDefault();
        if (!fileInput.files.length) { showError('Pilih file PDF terlebih dahulu.'); return; }
        if (!selectedBank)           { showError('Pilih bank terlebih dahulu.');      return; }

        errorBox.style.display    = 'none';
        loadingState.style.display = 'block';
        submitBtn.disabled         = true;
        stepIdx = 0;
        loadingText.textContent    = loadingSteps[0];
        stepTimer = setInterval(() => {
            stepIdx = (stepIdx + 1) % loadingSteps.length;
            loadingText.textContent = loadingSteps[stepIdx];
        }, 1800);

        const formData = new FormData();
        for (const f of fileInput.files) {
            formData.append('file', f);
        }
        formData.append('bank_code', selectedBank);

        try {
            const response = await fetch('/upload', { method: 'POST', body: formData });
            clearInterval(stepTimer);

            if (response.ok) {
                const cd     = response.headers.get('Content-Disposition') || '';
                const match  = cd.match(/filename="?([^"]+)"?/);
                const dlName = match ? match[1] : 'XR_Report.xlsx';

                const blob = await response.blob();
                const url  = window.URL.createObjectURL(blob);
                const a    = document.createElement('a');
                a.href = url; a.download = dlName;
                document.body.appendChild(a); a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);

                loadingText.textContent = '✓ Selesai — file terunduh.';
                setTimeout(() => {
                    loadingState.style.display = 'none';
                    fileInput.value = '';
                    fileName.textContent = '';
                    uploadLabel.textContent = 'Klik atau drop file di sini';
                    uploadHint.textContent  = 'Format PDF · Maks 16 MB';
                    uploadZone.classList.remove('has-file');
                    iconPdf.style.display   = 'block';
                    iconCheck.style.display = 'none';
                    checkReady();
                }, 2500);
            } else {
                const err = await response.text();
                showError('Error: ' + err);
                loadingState.style.display = 'none';
            }
        } catch (err) {
            clearInterval(stepTimer);
            showError('Koneksi gagal: ' + err.message);
            loadingState.style.display = 'none';
        } finally {
            submitBtn.disabled = !selectedBank;
        }
    });
</script>
</body>
</html>
'''

# ============================================================
# FLASK ROUTES
# ============================================================

@app.route('/')
def index():
    banks = get_enabled_banks()
    # Tambahkan bank nonaktif (coming soon) untuk ditampilkan di UI
    coming_soon = {
        'bri': {
            'name': 'Bank BRI', 'short_name': 'BRI',
            'color': '#003DA5', 'logo_text': 'BRI',
            'description': 'Segera hadir', 'enabled': False,
        },
    }
    all_banks = {**banks, **coming_soon}
    return render_template_string(HTML_TEMPLATE, banks=all_banks)


@app.route('/upload', methods=['POST'])
def upload_file():
    bank_code = request.form.get('bank_code', '').strip()
    if not bank_code:
        return 'Pilih bank terlebih dahulu.', 400

    # Mendukung upload beberapa PDF sekaligus (mis. tiap file 1-3 bulan,
    # tidak perlu di-merge manual dulu jadi satu PDF) — lihat
    # engine/multi_pdf_merger.py untuk aturan validasinya (rekening harus
    # sama, bulan tidak boleh bentrok, total maks 6 bulan).
    files = [f for f in request.files.getlist('file') if f.filename]
    if not files:
        return 'Tidak ada file yang dipilih.', 400
    for f in files:
        if not f.filename.lower().endswith('.pdf'):
            return f"File '{f.filename}' harus berformat PDF.", 400

    try:
        ExtractorClass = get_extractor(bank_code)
    except ValueError as e:
        return str(e), 400

    saved_paths = []
    try:
        per_file_results = []
        first_extractor = None

        for idx, f in enumerate(files):
            # Prefix indeks supaya nama file yang sama dari beberapa upload
            # tidak saling menimpa di UPLOAD_FOLDER.
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], f'{idx}_{f.filename}')
            f.save(filepath)
            saved_paths.append(filepath)

            extractor = ExtractorClass(filepath)
            if first_extractor is None:
                first_extractor = extractor

            saldo = extractor.extract_saldo()
            if not saldo:
                return f"Gagal mengekstrak data saldo dari '{f.filename}'. Pastikan format PDF sesuai.", 500
            trans = extractor.extract_transaksi()
            per_file_results.append((f.filename, saldo, trans))

        try:
            saldo_per_bulan, transaksi_per_bulan = merge_extractions(per_file_results)
        except MergeValidationError as e:
            return str(e), 400

        no_rekening = saldo_per_bulan.get('_no_rekening') or 'unknown'
        file_prefix = first_extractor.get_file_prefix()
        nama_file   = f'{file_prefix}_{no_rekening}.xlsx'

        output_path = os.path.join(app.config['EXPORT_FOLDER'], nama_file)
        create_excel(
            saldo_per_bulan,
            transaksi_per_bulan,
            output_path,
            bank_name=file_prefix,
            pdf_path=saved_paths,
        )

        for p in saved_paths:
            os.remove(p)

        return send_file(
            output_path,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=nama_file,
        )

    except Exception as e:
        # Give some time for file handles to close (Windows fix)
        import time
        time.sleep(0.1)
        for p in saved_paths:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except:
                    pass
        return f'Error: {str(e)}', 500


if __name__ == '__main__':
    print("\n" + "=" * 50)
    print("🚀 XR-App · eXtract-Report v2.0")
    print("=" * 50)
    print("\n📍 Akses aplikasi di: http://localhost:5000")
    print("📍 Atau: http://127.0.0.1:5000")
    print(f"\n🏦 Bank tersedia: {', '.join(get_enabled_banks().keys())}")
    print("\n⏹️  Tekan CTRL+C untuk stop server\n")
    app.run(debug=True, host='0.0.0.0', port=5000)
