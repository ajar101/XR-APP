// app.js — interaksi halaman depan XR-App: pilih bank, pilih/drop berkas,
// kirim ke /upload, unduh hasilnya.
//
// Batas upload TIDAK ditulis ulang di sini. Nilainya ditanam server lewat
// atribut data- pada <body> (lihat templates/index.html) yang bersumber dari
// app.config — dulu teks batasnya diketik langsung di JS dan tertinggal di
// "Maks 16 MB" setelah server dinaikkan ke 64 MB, jadi pemakai melihat batas
// yang salah setiap kali mengunggah berkas kedua.

let selectedBank = null;

// Batas upload ditanam server lewat atribut data- pada <body> — jangan
// diketik ulang di sini (lihat catatan di kepala berkas).
const MAKS_MB    = document.body.dataset.maksMb;
const MAKS_BULAN = document.body.dataset.maksBulan;
const LABEL_AWAL = 'Klik atau drop file di sini';
const HINT_AWAL  = `Bisa pilih lebih dari 1 PDF · Maks ${MAKS_BULAN} bulan mutasi total · Maks ${MAKS_MB} MB`;

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

function selectBank(el) {
    document.querySelectorAll('.bank-card').forEach(c => c.classList.remove('selected'));
    el.classList.add('selected');
    selectedBank = el.dataset.code;
    bankCodeInput.value = el.dataset.code;
    footerBankInfo.textContent = el.dataset.name;
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
                uploadLabel.textContent = LABEL_AWAL;
                uploadHint.textContent  = HINT_AWAL;
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
