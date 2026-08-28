// ==================== PAPERS FROM BACKEND ====================
const papers = typeof window.PAPERS_FROM_BACKEND !== 'undefined'
  ? window.PAPERS_FROM_BACKEND
  : [];

// ==================== VIEW PAPERS PAGE FUNCTIONALITY ====================
if (document.getElementById('semFolders')) {
    const papersGrid         = document.getElementById('papersGrid');
    const noResults          = document.getElementById('noResults');
    const resultsCount       = document.getElementById('resultsCount');
    const semesterFilter     = document.getElementById('semesterFilter');
    const semFolders         = document.getElementById('semFolders');
    const folderPapersView   = document.getElementById('folderPapersView');
    const btnBackFolders     = document.getElementById('btnBackFolders');
    const folderPapersHeader = document.getElementById('folderPapersHeader');

    let activeSem  = null;
    let activeType = null;
    let skipHashUpdate = false;

    const EXAM_TYPES  = ['SEA I', 'SEA II', 'ISA'];
    const EXAM_ICONS  = { 'SEA I': '📄', 'SEA II': '📋', 'ISA': '📝' };
    const EXAM_COLORS = { 'SEA I': 'blue', 'SEA II': 'purple', 'ISA': 'green' };
    const SLUG_TO_EXAM_TYPE = { 'sea-i': 'SEA I', 'sea-ii': 'SEA II', 'isa': 'ISA' };

    function examTypeToSlug(type) {
        return type.toLowerCase().replace(/\s+/g, '-');
    }

    function syncHash() {
        if (skipHashUpdate) return;
        let hash = '';
        if (activeSem != null) {
            hash = activeType
                ? `#sem-${activeSem}-${examTypeToSlug(activeType)}`
                : `#sem-${activeSem}`;
        }
        const nextUrl = window.location.pathname + window.location.search + hash;
        const currentUrl = window.location.pathname + window.location.search + window.location.hash;
        if (nextUrl === currentUrl) return;
        history.pushState({ sem: activeSem, type: activeType }, '', nextUrl);
    }

    function parseHash() {
        const raw = location.hash.replace(/^#/, '');
        if (!raw) return { level: 'folders' };
        const match = raw.match(/^sem-(\d+)(?:-(.+))?$/);
        if (!match) return { level: 'folders' };
        const sem = parseInt(match[1], 10);
        const typeSlug = match[2];
        if (typeSlug) {
            const type = SLUG_TO_EXAM_TYPE[typeSlug.toLowerCase()];
            if (type) return { level: 'papers', sem, type };
        }
        return { level: 'semester', sem };
    }

    function applyHash() {
        const parsed = parseHash();
        skipHashUpdate = true;
        try {
            if (parsed.level === 'folders') {
                activeSem = null;
                activeType = null;
                semesterFilter.value = '';
                renderFolders();
            } else if (parsed.level === 'semester') {
                openFolder(parsed.sem);
            } else if (parsed.level === 'papers') {
                openExamType(parsed.sem, parsed.type);
            }
        } finally {
            skipHashUpdate = false;
        }
    }

    window.addEventListener('popstate', applyHash);

    // ---- Render semester folders ----
    function renderFolders() {
        semFolders.style.display = 'grid';
        folderPapersView.style.display = 'none';
        const sems = [1,2,3,4,5,6];
        semFolders.innerHTML = sems.map(sem => {
            const count = papers.filter(p => String(p.semester) === String(sem)).length;
            const isEmpty = count === 0;
            return `
                <div class="sem-folder-card ${isEmpty ? 'sem-folder-empty' : ''}"
                     onclick="${isEmpty ? '' : `openFolder(${sem})`}">
                    ${!isEmpty ? `
                    <button class="sem-folder-download-btn"
                            onclick="event.stopPropagation(); downloadSemester(${sem})"
                            title="Download all Semester ${sem} papers"
                            aria-label="Download all Semester ${sem} papers">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                            <polyline points="7 10 12 15 17 10"></polyline>
                            <line x1="12" y1="15" x2="12" y2="3"></line>
                        </svg>
                    </button>` : ''}
                    <div class="sem-folder-icon-wrap">
                        <svg viewBox="0 0 48 40" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <rect x="0" y="8" width="48" height="30" rx="4" fill="${isEmpty ? '#D1D5DB' : '#BFDBFE'}"/>
                            <rect x="0" y="12" width="48" height="26" rx="4" fill="${isEmpty ? '#E5E7EB' : '#DBEAFE'}"/>
                            <rect x="2" y="6" width="20" height="8" rx="3" fill="${isEmpty ? '#D1D5DB' : '#93C5FD'}"/>
                            <rect x="4" y="18" width="40" height="3" rx="1.5" fill="${isEmpty ? '#9CA3AF' : '#3B82F6'}" opacity="0.4"/>
                            <rect x="4" y="24" width="32" height="3" rx="1.5" fill="${isEmpty ? '#9CA3AF' : '#3B82F6'}" opacity="0.3"/>
                            <rect x="4" y="30" width="36" height="3" rx="1.5" fill="${isEmpty ? '#9CA3AF' : '#3B82F6'}" opacity="0.2"/>
                        </svg>
                    </div>
                    <div class="sem-folder-name">Semester ${sem}</div>
                    <div class="sem-folder-count">${count} ${count === 1 ? 'paper' : 'papers'}</div>
                    ${isEmpty ? `
                    <a href="/user-upload" class="upload-hint-btn" onclick="event.stopPropagation()" title="No papers yet — be the first to upload!">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                            <polyline points="17 8 12 3 7 8"/>
                            <line x1="12" y1="3" x2="12" y2="15"/>
                        </svg>
                        Upload
                    </a>` : ''}
                </div>
            `;
        }).join('');
        syncHash();
    }

    // ---- Open semester → show exam type cards ----
    window.openFolder = function(sem) {
        activeSem  = sem;
        activeType = null;
        semFolders.style.display = 'none';
        folderPapersView.style.display = 'block';

        const typeCards = EXAM_TYPES.map(type => {
            const count = papers.filter(p =>
                String(p.semester) === String(sem) &&
                (p.examType || '').toUpperCase() === type.toUpperCase()
            ).length;
            const isEmpty = count === 0;
            const color   = EXAM_COLORS[type];
            return `
                <div class="exam-type-card exam-type-${color} ${isEmpty ? 'exam-type-empty' : ''}"
                     onclick="${isEmpty ? '' : `openExamType(${sem}, '${type}')`}">
                    <div class="exam-type-icon">${EXAM_ICONS[type]}</div>
                    <div class="exam-type-name">${type}</div>
                    <div class="exam-type-count">${count} ${count === 1 ? 'paper' : 'papers'}</div>
                    ${isEmpty ? `
                    <a href="/user-upload" class="upload-hint-btn upload-hint-sm" onclick="event.stopPropagation()" title="No papers yet — be the first to upload!">
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                            <polyline points="17 8 12 3 7 8"/>
                            <line x1="12" y1="3" x2="12" y2="15"/>
                        </svg>
                        Upload
                    </a>` : ''}
                </div>
            `;
        }).join('');

        const totalCount = papers.filter(p => String(p.semester) === String(sem)).length;
        folderPapersHeader.innerHTML = `
            <div class="folder-breadcrumb">
                <button class="breadcrumb-btn" onclick="renderFolders()">All Semesters</button>
                <span class="breadcrumb-sep">›</span>
                <span class="breadcrumb-current">Semester ${sem}</span>
            </div>
            <div class="folder-title-row">
                <h2>📂 Semester ${sem}</h2>
                <span class="folder-total-badge">${totalCount} total papers</span>
            </div>
            <div class="exam-type-grid" id="examTypeGrid">${typeCards}</div>
        `;

        papersGrid.style.display = 'none';
        noResults.style.display  = 'none';
        resultsCount.textContent = '';
        syncHash();
    };

    // ---- Open exam type → show papers ----
    window.openExamType = function(sem, type) {
        activeSem  = sem;
        activeType = type;
        semesterFilter.value = String(sem);
        semFolders.style.display = 'none';
        folderPapersView.style.display = 'block';

        folderPapersHeader.innerHTML = `
            <div class="folder-breadcrumb">
                <button class="breadcrumb-btn" onclick="renderFolders()">All Semesters</button>
                <span class="breadcrumb-sep">›</span>
                <button class="breadcrumb-btn" onclick="openFolder(${sem})">Semester ${sem}</button>
                <span class="breadcrumb-sep">›</span>
                <span class="breadcrumb-current">${type}</span>
            </div>
            <div class="folder-title-row">
                <h2>${EXAM_ICONS[type]} ${type} — Semester ${sem}</h2>
            </div>
        `;

        renderPapers(type);
        syncHash();
    };

    // ---- Back button (uses browser history when hash nav is active) ----
    function goBackInBrowse() {
        if (activeSem != null || activeType != null) {
            history.back();
            return;
        }
        renderFolders();
    }

    btnBackFolders.addEventListener('click', goBackInBrowse);

    // ---- Floating back button (optional null check) ----
    const btnBackFloat = document.getElementById('btnBackFloat');
    if (btnBackFloat) {
        btnBackFloat.addEventListener('click', goBackInBrowse);
        window.addEventListener('scroll', () => {
            if (folderPapersView.style.display === 'none') {
                btnBackFloat.classList.remove('visible');
                return;
            }
            const rect = btnBackFolders.getBoundingClientRect();
            btnBackFloat.classList.toggle('visible', rect.bottom < 0);
        }, { passive: true });
    }

    // ---- Render papers (folder/exam-type driven, no legacy filter dropdowns) ----
    function renderPapers(forceType) {
        const semesterValue = semesterFilter.value;
        const typeValue     = forceType || activeType;
        const filteredPapers = papers.filter(paper => {
            const matchSemester = !semesterValue || String(paper.semester) === String(semesterValue);
            const matchType     = !typeValue     || (paper.examType || '').toUpperCase() === typeValue.toUpperCase();
            return matchSemester && matchType;
        });
        filteredPapers.sort((a, b) => (a.subject || '').localeCompare(b.subject || '', undefined, { sensitivity: 'base', numeric: true }));
        renderPaperCards(filteredPapers);
    }

    // ---- Shared paper-card renderer (used by both folder browse and unified search) ----
    function renderPaperCards(list) {
        const count = list.length;
        resultsCount.textContent = `Showing ${count} ${count === 1 ? 'paper' : 'papers'}`;

        if (list.length === 0) {
            papersGrid.style.display = 'none';
            noResults.style.display  = 'block';
            return;
        }
        papersGrid.style.display = 'grid';
        noResults.style.display  = 'none';
        papersGrid.innerHTML = list.map(paper => `
            <div class="paper-card">
                <div class="paper-info">
                    <div class="info-item">
                        <span class="info-label">Subject</span>
                        <span class="info-value">${escapeHtml(paper.subject)}</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">Year</span>
                        <span class="info-value">${escapeHtml(String(paper.year))}</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">Semester</span>
                        <span class="info-value">Sem ${escapeHtml(String(paper.semester || ''))}</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">Exam Type</span>
                        <span class="info-value">${escapeHtml(paper.examType || '—')}</span>
                    </div>
                </div>
                                <div class="paper-card-actions">
                    <a href="${escapeHtml(paper.file_url || '')}" class="btn-view-paper" target="_blank">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                            <circle cx="12" cy="12" r="3"></circle>
                        </svg>
                        View
                    </a>
                    <button class="btn-download-paper" onclick="forceDownload('${escapeHtml(paper.file_url || '')}', '${escapeHtml(paper.subject)}_${escapeHtml(String(paper.year))}.pdf')">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                            <polyline points="7 10 12 15 17 10"></polyline>
                            <line x1="12" y1="15" x2="12" y2="3"></line>
                        </svg>
                        Download
                    </button>
                    <button class="btn-analyse-paper" onclick="analysePaper(${paper.paper_id}, '${escapeHtml(paper.subject)}')">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                             <circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/>
                        </svg>
                        Analyse
                    </button>
                </div>
            </div>
        `).join('');
    }

    let analyseController = null;

window.analysePaper = async function(paperId, subject) {
    if (analyseController) analyseController.abort();
    analyseController = new AbortController();

    document.getElementById('analyseSubject').textContent = subject;
    document.getElementById('analyseResult').innerHTML = `
        <div style="display:flex; flex-direction:column; align-items:center;
                    justify-content:center; padding:3rem; gap:1rem; color:#6B7280;">
            <svg style="animation:spin 1s linear infinite" width="36" height="36"
                 viewBox="0 0 24 24" fill="none" stroke="#8B5CF6" stroke-width="2">
                <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
            </svg>
            <span style="font-weight:600;">Analysing paper with AI…</span>
            <span style="font-size:0.8rem;">This may take 15–20 seconds</span>
            <button onclick="closeAnalyseModal()"
                style="margin-top:0.5rem; padding:0.4rem 1.2rem; border-radius:8px;
                       border:1px solid #E5E7EB; background:white; cursor:pointer;
                       font-family:inherit; font-size:0.85rem; color:#6B7280;">
                Cancel
            </button>
        </div>`;
    document.getElementById('analyseModal').style.display = 'flex';

    try {
        const res = await fetch(`/analyze/${paperId}`, { signal: analyseController.signal });
        const data = await res.json();
        if (data.error) {
            document.getElementById('analyseResult').innerHTML =
                `<p style="color:#EF4444;">⚠️ ${data.error}</p>`;
            return;
        }
        document.getElementById('analyseSubject').textContent =
            data.subject + ' — ' + data.exam_type + ' ' + data.year;
        document.getElementById('analyseResult').innerHTML =
            marked.parse(data.predictions);
    } catch(e) {
        if (e.name === 'AbortError') return;
        document.getElementById('analyseResult').innerHTML =
            `<p style="color:#EF4444;">⚠️ Analysis failed. Please try again.</p>`;
    }
};

window.closeAnalyseModal = function() {
    if (analyseController) { analyseController.abort(); analyseController = null; }
    document.getElementById('analyseModal').style.display = 'none';
};

    function escapeHtml(text) {
        if (text == null) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // ---- View toggle ----
    const viewBtns = document.querySelectorAll('.view-btn');
    viewBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            viewBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const view = btn.dataset.view;
            if (view === 'list') {
                papersGrid.style.gridTemplateColumns = '1fr';
                papersGrid.classList.add('view-list');
                papersGrid.classList.remove('view-grid');
            } else {
                papersGrid.style.gridTemplateColumns = '';
                papersGrid.classList.add('view-grid');
                papersGrid.classList.remove('view-list');
            }
        });
    });

    // ---- Bulk semester download (JSZip — single ZIP file, works in all browsers) ----
    // Fetches all PDFs as blobs in parallel, bundles them into one ZIP, single download.
    // This bypasses the browser multi-download block that limited the old approach to 1 file.
    let isDownloadingSem = false;

    window.downloadSemester = async function(sem) {
        if (isDownloadingSem) return;
        const semPapers = papers.filter(p => String(p.semester) === String(sem) && p.file_url);
        if (semPapers.length === 0) return;

        if (!window.JSZip) {
            showToast('⚠️ JSZip not loaded yet — try again in a moment.');
            return;
        }

        isDownloadingSem = true;

        const zip = new JSZip();
        let done = 0;
        showToast(`⬇️ Fetching paper 1 of ${semPapers.length}…`);

        // Fetch all in parallel (one promise per paper)
        const fetchPromises = semPapers.map(async (p) => {
            const filename = `${sanitizeFilename(p.subject)}_${sanitizeFilename(p.examType || 'paper')}_${p.year}.pdf`;
            try {
                // Use /proxy-pdf to avoid CORS/CSP block on direct Supabase fetch
                const proxyUrl = '/proxy-pdf?url=' + encodeURIComponent(p.file_url);
                const res = await fetch(proxyUrl);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const blob = await res.blob();
                zip.file(filename, blob);
            } catch (err) {
                console.warn(`Skipped ${filename}:`, err);
            }
            done++;
            showToast(`⬇️ Fetching paper ${done} of ${semPapers.length}…`);
        });

        await Promise.all(fetchPromises);

        if (Object.keys(zip.files).length === 0) {
            showToast('⚠️ No papers could be fetched. Check your connection.');
            isDownloadingSem = false;
            return;
        }

        showToast('📦 Building ZIP…');
        const zipBlob = await zip.generateAsync({ type: 'blob' });
        saveAs(zipBlob, `Semester_${sem}_Papers.zip`);

        isDownloadingSem = false;
        showToast(`✅ Semester ${sem} — ${Object.keys(zip.files).length} papers downloaded!`);
    };

    function sanitizeFilename(name) {
        return String(name || 'Subject').replace(/[\/\\?%*:|"<>]/g, '-').trim();
    }

    // ---- Unified search (replaces old Subject/Year/Search filter dropdowns) ----
    // Deliberately never touches location.hash — transient in-memory view.
    // Clearing the box calls applyHash() to restore the prior hash-based view.
    const unifiedSearch = document.getElementById('unifiedSearch');
    let searchDebounceTimer = null;

    if (unifiedSearch) {
        unifiedSearch.addEventListener('input', () => {
            clearTimeout(searchDebounceTimer);
            searchDebounceTimer = setTimeout(() => {
                const q = unifiedSearch.value.trim().toLowerCase();
                if (!q) {
                    applyHash(); // restore whatever folder level the hash points to
                    return;
                }
                renderSearchResults(q);
            }, 120);
        });
    }

    function renderSearchResults(q) {
        const matches = papers.filter(p =>
            (p.subject  && p.subject.toLowerCase().includes(q))  ||
            (p.examType && p.examType.toLowerCase().includes(q)) ||
            String(p.year     || '').includes(q) ||
            String(p.semester || '').includes(q)
        );
        matches.sort((a, b) => (a.subject || '').localeCompare(b.subject || '', undefined, { sensitivity: 'base', numeric: true }));
        semFolders.style.display = 'none';
        folderPapersView.style.display = 'block';
        folderPapersHeader.innerHTML = `
            <div class="folder-title-row">
                <h2>🔍 Search results for "${escapeHtml(q)}"</h2>
                <span class="folder-total-badge">${matches.length} found</span>
            </div>`;
        renderPaperCards(matches);
    }

    // ---- Initial render (restore from hash if present) ----
    applyHash();
}

// ==================== UPLOAD PAGE FUNCTIONALITY ====================
if (document.getElementById('uploadForm')) {
    const uploadForm     = document.getElementById('uploadForm');
    const uploadFile     = document.getElementById('uploadFile') || document.getElementById('fileInput');
    const fileUploadArea = document.getElementById('fileUploadArea');
    const fileSelected   = document.getElementById('fileSelected');
    const fileName       = document.getElementById('fileName');
    const fileSize       = document.getElementById('fileSize');
    const removeFile     = document.getElementById('removeFile');

    // ── Department → Subject filter ──
    // Only runs on admin /upload page (uploadDepartment).
    // The /user-upload page has its own dept+semester dual filter inline.
    (function () {
        const deptSelect    = document.getElementById('uploadDepartment');
        const subjectSelect = document.getElementById('uploadSubject');
        if (!deptSelect || !subjectSelect) return;

        const allSubjectOptions = Array.from(subjectSelect.querySelectorAll('option')).filter(o => o.value);

        function filterSubjectsByDept(deptName) {
            subjectSelect.innerHTML = '<option value="">' +
                (deptName ? '— Select a subject —' : '— Select department first —') +
                '</option>';
            if (!deptName) return;
            let found = 0;
            allSubjectOptions.forEach(opt => {
                if (opt.dataset.department === deptName) {
                    subjectSelect.appendChild(opt.cloneNode(true));
                    found++;
                }
            });
            if (found === 0) {
                const empty = document.createElement('option');
                empty.disabled = true;
                empty.textContent = 'No subjects for this department';
                subjectSelect.appendChild(empty);
            }
        }

        deptSelect.addEventListener('change', function () {
            filterSubjectsByDept(this.value);
            subjectSelect.value = '';
            const semSelect = document.getElementById('uploadSemester');
            if (semSelect) semSelect.value = '';
        });

        // Restore filtered list on error re-render (dept already selected via POST)
        if (deptSelect.value) filterSubjectsByDept(deptSelect.value);
    })();

    // ── Subject → Semester auto-fill (admin /upload only) ──
    (function () {
        const subjectSelect  = document.getElementById('uploadSubject');
        const semesterSelect = document.getElementById('uploadSemester');
        if (!subjectSelect || !semesterSelect) return;

        subjectSelect.addEventListener('change', function () {
            const selected = this.options[this.selectedIndex];
            const sem = selected ? selected.dataset.semester : '';
            if (sem) {
                semesterSelect.value = sem;
                semesterSelect.style.transition = 'background .3s ease';
                semesterSelect.style.background = 'rgba(59,130,246,0.08)';
                setTimeout(() => { semesterSelect.style.background = '#f3f4f6'; }, 600);
            } else {
                semesterSelect.value = '';
            }
        });
    })();

    if (fileUploadArea) {
        fileUploadArea.addEventListener('click', () => uploadFile.click());

        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            fileUploadArea.addEventListener(eventName, e => { e.preventDefault(); e.stopPropagation(); }, false);
        });

        ['dragenter', 'dragover'].forEach(eventName => {
            fileUploadArea.addEventListener(eventName, () => {
                fileUploadArea.style.borderColor = 'var(--primary-blue)';
                fileUploadArea.style.background = 'rgba(37, 99, 235, 0.05)';
            }, false);
        });

        ['dragleave', 'drop'].forEach(eventName => {
            fileUploadArea.addEventListener(eventName, () => {
                fileUploadArea.style.borderColor = '';
                fileUploadArea.style.background = '';
            }, false);
        });

        fileUploadArea.addEventListener('drop', (e) => {
            const files = e.dataTransfer.files;
            if (files.length > 0) { uploadFile.files = files; handleFileSelect(files[0]); }
        }, false);
    }

    if (uploadFile) {
        uploadFile.addEventListener('change', (e) => {
            if (e.target.files[0]) handleFileSelect(e.target.files[0]);
        });
    }

    function handleFileSelect(file) {
        if (file.type !== 'application/pdf') {
            showError('fileError', 'Only PDF files are allowed');
            uploadFile.value = ''; return;
        }
        if (file.size > 10 * 1024 * 1024) {
            showError('fileError', 'File size must not exceed 10MB');
            uploadFile.value = ''; return;
        }
        if (fileName) fileName.textContent = file.name;
        if (fileSize) fileSize.textContent = formatFileSize(file.size);
        const uploadContent = document.querySelector('.file-upload-content');
        if (uploadContent) uploadContent.style.display = 'none';
        if (fileSelected) fileSelected.style.display = 'flex';
        hideError('fileError');
    }

    if (removeFile) {
        removeFile.addEventListener('click', (e) => {
            e.stopPropagation();
            uploadFile.value = '';
            const uploadContent = document.querySelector('.file-upload-content');
            if (uploadContent) uploadContent.style.display = 'block';
            if (fileSelected) fileSelected.style.display = 'none';
        });
    }

    function formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
    }

    function showError(id, msg) {
        const el = document.getElementById(id);
        if (el) { el.textContent = msg; el.style.display = 'block'; }
    }

    function hideError(id) {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    }

    uploadForm.addEventListener('submit', (e) => {
        e.preventDefault();
        document.querySelectorAll('.error-message').forEach(el => el.style.display = 'none');

        let isValid = true;
        const deptEl = document.getElementById('uploadDepartment');
        const subEl  = document.getElementById('uploadSubject');
        const semEl  = document.getElementById('uploadSemester');
        const yearEl = document.getElementById('uploadYear');
        const exEl   = document.getElementById('uploadExamType');

        if (deptEl && !deptEl.value)  { showError('departmentError', 'Please select a department'); isValid = false; }
        if (subEl  && !subEl.value)   { showError('subjectError',    'Please select a subject');    isValid = false; }
        if (yearEl && !yearEl.value)  { showError('yearError',       'Please select a year');       isValid = false; }
        if (semEl  && !semEl.value)   { showError('semesterError',   'Please select a semester');   isValid = false; }
        if (exEl   && !exEl.value)    { showError('examTypeError',   'Please select an exam type'); isValid = false; }

        const file = uploadFile ? uploadFile.files[0] : null;
        if (!file) { showError('fileError', 'Please select a PDF file'); isValid = false; }
        else if (file.type !== 'application/pdf') { showError('fileError', 'Only PDF files are allowed'); isValid = false; }

        if (isValid) uploadForm.submit();
    });
}

// ==================== SMOOTH SCROLL ====================
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function(e) {
        const href = this.getAttribute('href');
        if (href !== '#' && href !== '') {
            e.preventDefault();
            const target = document.querySelector(href);
            if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    });
});

// ==================== MOBILE MENU TOGGLE ====================
document.addEventListener('DOMContentLoaded', function() {
    const mobileMenuToggle = document.getElementById('mobileMenuToggle');
    const mobileNav = document.getElementById('mobileNav');

    if (mobileMenuToggle && mobileNav) {
        mobileMenuToggle.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            this.classList.toggle('active');
            mobileNav.classList.toggle('active');
            document.body.style.overflow = mobileNav.classList.contains('active') ? 'hidden' : '';
        });

        mobileNav.querySelectorAll('.mobile-nav-link').forEach(link => {
            link.addEventListener('click', function() {
                mobileMenuToggle.classList.remove('active');
                mobileNav.classList.remove('active');
                document.body.style.overflow = '';
            });
        });

        document.addEventListener('click', function(e) {
            if (mobileNav.classList.contains('active') &&
                !mobileMenuToggle.contains(e.target) &&
                !mobileNav.contains(e.target)) {
                mobileMenuToggle.classList.remove('active');
                mobileNav.classList.remove('active');
                document.body.style.overflow = '';
            }
        });
    }

    const currentPath = window.location.pathname;
    document.querySelectorAll('.nav-link, .mobile-nav-link').forEach(link => {
        const href = link.getAttribute('href');
        if (href && (href === currentPath || currentPath.includes(href.split('?')[0]))) {
            link.classList.add('active');
        }
    });
});