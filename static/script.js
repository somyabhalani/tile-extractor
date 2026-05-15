document.addEventListener('DOMContentLoaded', () => {
    const fileInput = document.getElementById('file-input');
    const dropZone = document.getElementById('drop-zone');
    const uploadSection = document.getElementById('upload-section');
    const loadingSection = document.getElementById('loading-section');
    const resultsSection = document.getElementById('results-section');
    const progressFill = document.getElementById('progress-fill');
    const progressText = document.getElementById('progress-text');
    const gallery = document.getElementById('gallery');
    const statCount = document.getElementById('stat-count');
    const downloadAllBtn = document.getElementById('download-all-btn');

    // Lightbox Elements
    const lightbox = document.getElementById('lightbox');
    const lightboxImg = document.getElementById('lightbox-img');
    const closeBtn = document.querySelector('.close-btn');
    const lbFilename = document.getElementById('lb-filename');
    const lbDims = document.getElementById('lb-dims');
    const lbSize = document.getElementById('lb-size');
    const lbFormat = document.getElementById('lb-format');
    const lbPage = document.getElementById('lb-page');
    const lbDownload = document.getElementById('lb-download');
    const lbProductWrap = document.getElementById('lb-product-wrap');
    const lbProductText = document.getElementById('lb-product-text');

    // Text Info Modal Elements
    const textModal = document.getElementById('text-modal');
    const closeTextModal = document.getElementById('close-text-modal');
    const textModalTitle = document.getElementById('text-modal-title');
    const textModalContent = document.getElementById('text-modal-content');

    let currentJobId = null;
    // Store scanned text per page: { pageNum: { xref: "text" } }
    let scannedTextData = {};

    // --- Helpers ---
    function formatBytes(bytes, decimals = 2) {
        if (!bytes || bytes === 0) return '0 Bytes';
        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    }

    // Extract xref from filename: tile_p0001_600x1200_123.jpg -> 123
    function getXrefFromFilename(filename) {
        const parts = filename.rsplit ? filename.rsplit('_', 1) : filename.split('_');
        const last = parts[parts.length - 1];
        return parseInt(last.split('.')[0], 10);
    }

    // --- Upload Logic ---
    async function handleUpload(file) {
        if (!file || !file.name.toLowerCase().endsWith('.pdf')) {
            alert('Please select a valid PDF file.');
            return;
        }

        const formData = new FormData();
        formData.append('file', file);

        uploadSection.classList.add('hidden');
        loadingSection.classList.remove('hidden');
        progressFill.style.width = '0%';
        progressText.textContent = '0%';
        scannedTextData = {};

        try {
            const response = await fetch('/api/upload', { method: 'POST', body: formData });
            const data = await response.json();
            currentJobId = data.job_id;
            pollProgress(currentJobId);
        } catch (error) {
            alert('Upload failed: ' + error);
            uploadSection.classList.remove('hidden');
            loadingSection.classList.add('hidden');
        }
    }

    dropZone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
        if (fileInput.files.length > 0) handleUpload(fileInput.files[0]);
    });
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('drag-active'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-active'));
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('drag-active');
        if (e.dataTransfer.files.length > 0) handleUpload(e.dataTransfer.files[0]);
    });

    // --- Polling ---
    function pollProgress(jobId) {
        const interval = setInterval(async () => {
            try {
                const response = await fetch(`/api/progress/${jobId}`);
                const data = await response.json();
                if (data.status === 'completed') {
                    clearInterval(interval);
                    fetchResults(jobId);
                } else if (data.status === 'error') {
                    clearInterval(interval);
                    alert('Error: ' + data.error);
                    uploadSection.classList.remove('hidden');
                    loadingSection.classList.add('hidden');
                } else if (data.status === 'unknown') {
                    clearInterval(interval);
                    alert('Session lost. Please re-upload.');
                    uploadSection.classList.remove('hidden');
                    loadingSection.classList.add('hidden');
                } else {
                    progressFill.style.width = `${data.percentage}%`;
                    progressText.textContent = `${data.percentage}%`;
                }
            } catch (error) { console.error('Polling error:', error); }
        }, 1000);
    }

    // --- Results ---
    async function fetchResults(jobId) {
        try {
            const response = await fetch(`/api/results/${jobId}`);
            const data = await response.json();
            renderGallery(data.pages, jobId);
            loadingSection.classList.add('hidden');
            resultsSection.classList.remove('hidden');
            let totalTiles = 0;
            data.pages.forEach(p => totalTiles += p.images.length);
            statCount.textContent = totalTiles;
            downloadAllBtn.href = `/api/download/${jobId}`;
            downloadAllBtn.style.display = 'inline-flex';
        } catch (error) { alert('Failed to fetch results: ' + error); }
    }

    // --- Gallery ---
    function renderGallery(pages, jobId) {
        gallery.innerHTML = '';
        pages.forEach(pageData => {
            const group = document.createElement('div');
            group.className = 'page-group';

            // Page header with scan button and container for info button
            const header = document.createElement('div');
            header.className = 'page-header';
            header.innerHTML = `
                <div class="page-number">Page ${pageData.page}</div>
                <div class="page-actions" id="page-actions-${pageData.page}">
                    <button class="btn-scan-text" data-page="${pageData.page}" data-job="${jobId}">
                        <i class="fa-solid fa-magnifying-glass"></i> Scan Text
                    </button>
                </div>
            `;
            group.appendChild(header);

            const grid = document.createElement('div');
            grid.className = 'masonry-grid';

            pageData.images.forEach(img => {
                const item = document.createElement('div');
                item.className = 'tile-card';
                item.dataset.xref = getXrefFromFilename(img.filename);
                item.dataset.page = pageData.page;

                const imgSrc = `/api/images/${jobId}/${img.filename}`;
                item.innerHTML = `
                    <img src="${imgSrc}" alt="Tile" loading="lazy">
                    <div class="tile-overlay">
                        <p>Tile Image</p>
                        <p class="dim">${img.width} x ${img.height} px</p>
                    </div>
                `;
                item.addEventListener('click', () => openLightbox(img, jobId, item));
                grid.appendChild(item);
            });

            group.appendChild(grid);
            gallery.appendChild(group);
        });

        // Wire up all Scan Text buttons
        document.querySelectorAll('.btn-scan-text').forEach(btn => {
            btn.addEventListener('click', () => scanPageText(btn));
        });
    }

    // --- Scan Text per page ---
    async function scanPageText(btn) {
        const page = btn.dataset.page;
        const jobId = btn.dataset.job;

        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Scanning...';

        try {
            const response = await fetch(`/api/scan-text/${jobId}/${page}`, { method: 'POST' });
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || 'Scan failed');
            }
            const data = await response.json();

            // Store scanned text data
            scannedTextData[page] = data.associations || {};

            // Update tile cards on this page with a small text indicator
            document.querySelectorAll(`.tile-card[data-page="${page}"]`).forEach(card => {
                const xref = card.dataset.xref;
                const text = scannedTextData[page][xref];
                if (text) {
                    const overlay = card.querySelector('.tile-overlay');
                    if (overlay) {
                        overlay.innerHTML += `<p class="tile-text-tag"><i class="fa-solid fa-tag"></i> Text found</p>`;
                    }
                }
            });

            btn.innerHTML = '<i class="fa-solid fa-check"></i> Scanned';
            btn.classList.add('btn-scan-done');

            // Add "Text Info" button
            const actionsContainer = document.getElementById(`page-actions-${page}`);
            if (actionsContainer && !document.getElementById(`btn-text-info-${page}`)) {
                const infoBtn = document.createElement('button');
                infoBtn.id = `btn-text-info-${page}`;
                infoBtn.className = 'btn-text-info';
                infoBtn.innerHTML = '<i class="fa-solid fa-list"></i> Page Text Info';
                infoBtn.addEventListener('click', () => openPageTextModal(page));
                actionsContainer.appendChild(infoBtn);
            }

        } catch (err) {
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-magnifying-glass"></i> Retry';
            alert('Scan failed: ' + err.message);
        }
    }

    // --- Lightbox ---
    function openLightbox(img, jobId, cardEl) {
        lightboxImg.src = `/api/images/${jobId}/${img.filename}`;
        lbFilename.textContent = img.filename;
        lbDims.textContent = `${img.width} x ${img.height} px`;
        lbSize.textContent = formatBytes(parseInt(img.size));
        lbFormat.textContent = img.format || 'N/A';
        lbPage.textContent = img.page;
        lbDownload.href = `/api/images/${jobId}/${img.filename}`;
        lbDownload.setAttribute('download', img.filename);

        // Show product text if scanned
        const xref = cardEl ? cardEl.dataset.xref : null;
        const page = img.page;
        const text = scannedTextData[page] && xref ? scannedTextData[page][xref] : null;

        if (text) {
            lbProductText.textContent = text;
            lbProductWrap.style.display = 'flex';
        } else {
            lbProductWrap.style.display = 'none';
        }

        lightbox.classList.add('active');
    }

    // --- Page Text Modal ---
    function openPageTextModal(page) {
        const textData = scannedTextData[page];
        textModalTitle.textContent = `Page ${page} - Extracted Product Text`;
        
        if (!textData || Object.keys(textData).length === 0) {
            textModalContent.innerHTML = "<em>No text was found or matched to tiles on this page.</em>";
        } else {
            let htmlContent = "";
            for (const [xref, text] of Object.entries(textData)) {
                if (text) {
                    // Split the text by the | delimiter and format nicely
                    const lines = text.split('|').map(l => l.trim()).filter(l => l);
                    let formattedText = lines.map(l => `• ${l}`).join('<br>');
                    
                    htmlContent += `
                        <div style="margin-bottom: 0.75rem; background: rgba(255,255,255,0.03); padding: 0.75rem; border-radius: 8px; border: 1px solid rgba(255,255,255,0.05);">
                            <h4 style="margin: 0 0 0.25rem 0; color: #fcd34d;">Tile Image Reference: ${xref}</h4>
                            <div style="padding-left: 0.5rem;">${formattedText}</div>
                        </div>
                    `;
                }
            }
            if (!htmlContent) {
                 htmlContent = "<em>No text was matched for the tiles on this page.</em>";
            }
            textModalContent.innerHTML = htmlContent;
        }
        
        textModal.classList.add('active');
    }

    // Modal Close Handlers
    closeBtn.addEventListener('click', () => lightbox.classList.remove('active'));
    lightbox.addEventListener('click', (e) => {
        if (e.target === lightbox) lightbox.classList.remove('active');
    });

    closeTextModal.addEventListener('click', () => textModal.classList.remove('active'));
    textModal.addEventListener('click', (e) => {
        if (e.target === textModal) textModal.classList.remove('active');
    });
});
