/* =====================================================================
   viewjs.js — Browse Papers page specific scripts for PYQ Portal
   ===================================================================== */

/* ── Popup ── */
const popupData = {
    help: {
        icon:   '🎓',
        iconBg: 'rgba(59,130,246,0.1)',
        title:  'Need Help?',
        desc:   'Having trouble finding papers or using the portal? Drop us an email and we\'ll get back to you as soon as possible.',
        mail:   'mailto:pyqportalhelp@gmail.com?subject=Help Request - PYQ Portal',
        label:  'pyqportalhelp@gmail.com'
    }
};

function openPopup(type) {
    const d = popupData[type];
    const icon = document.getElementById('popupIcon');
    icon.textContent = d.icon;
    icon.style.background = d.iconBg;
    document.getElementById('popupTitle').textContent     = d.title;
    document.getElementById('popupDesc').textContent      = d.desc;
    document.getElementById('popupMailBtn').href          = d.mail;
    document.getElementById('popupMailLabel').textContent = d.label;
    document.getElementById('popupOverlay').classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closePopup() {
    document.getElementById('popupOverlay').classList.remove('active');
    document.body.style.overflow = '';
}

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closePopup();
});

/* ── Staggered card entrance animation ── */
function animateCards() {
    const cards = document.querySelectorAll('#papersGrid .paper-card, #papersGrid > *');
    cards.forEach((card, i) => {
        card.classList.add('paper-card-anim');
        card.style.transitionDelay = `${i * 60}ms`;
        requestAnimationFrame(() => {
            requestAnimationFrame(() => card.classList.add('card-visible'));
        });
    });
}

/* ── Search / filter: re-animate cards after filtering ── */
function reAnimateCards() {
    const cards = document.querySelectorAll('#papersGrid .paper-card, #papersGrid > *');
    cards.forEach(card => {
        card.classList.remove('card-visible');
        card.style.transitionDelay = '0ms';
    });
    setTimeout(animateCards, 30);
}

/* Observe DOM mutations in papersGrid */
const grid = document.getElementById('papersGrid');
if (grid) {
    const mo = new MutationObserver(() => animateCards());
    mo.observe(grid, { childList: true });
}

/* ── Filter select: flash on change (no-op if no selects exist, safe) ── */
document.querySelectorAll('.filter-select').forEach(el => {
    el.addEventListener('change', function () {
        if (!this.value) return;
        this.style.transition = 'background 0.3s ease';
        this.style.background = 'rgba(59,130,246,0.06)';
        setTimeout(() => { this.style.background = ''; }, 450);
    });
});

/* ── Initial entrance after page load ── */
window.addEventListener('load', () => {
    setTimeout(animateCards, 300);
});

/* ── Toast notification ── */
function showToast(message, duration = 3000) {
    let toast = document.getElementById('pyqToast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'pyqToast';
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add('toast-visible');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => toast.classList.remove('toast-visible'), duration);
}

/* ── Force-download helper ──
   silent=true suppresses per-file toasts (used by bulk downloadSemester).
   Single-paper downloads call without silent (defaults to false). */
function forceDownload(url, filename, silent = false) {
    if (!silent) showToast('⬇️ Downloading… please wait');
    const a = document.createElement('a');
    const dlUrl = url.includes('/download') ? url : (url.replace('/view', '/download') + (url.includes('?') ? '&' : '?') + 'download=1');
    a.href = dlUrl;
    a.download = filename || 'question-paper.pdf';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    if (!silent) setTimeout(() => showToast('✅ Download started!'), 500);
}

window.copyAnalysis = function() {
    const text = document.getElementById('analyseResult').innerText;
    navigator.clipboard.writeText(text).then(() => {
        const btn = document.getElementById('copyBtn');
        btn.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#22C55E" stroke-width="2">
                <polyline points="20 6 9 17 4 12"/>
            </svg>
            Copied!`;
        btn.style.color = '#22C55E';
        setTimeout(() => {
            btn.innerHTML = `
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                </svg>
                Copy`;
            btn.style.color = '#6B7280';
        }, 2000);
    });
};

/* ── Search hero: clear button + chip active state ── */
(function () {
    const input     = document.getElementById('unifiedSearch');
    const clearBtn  = document.getElementById('searchClearBtn');
    const chips     = document.querySelectorAll('.search-chip');

    if (!input) return;

    function updateClearBtn() {
        if (clearBtn) clearBtn.style.display = input.value ? 'flex' : 'none';
    }

    input.addEventListener('input', updateClearBtn);

    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            input.value = '';
            input.dispatchEvent(new Event('input'));
            chips.forEach(c => c.classList.remove('active'));
            updateClearBtn();
            input.focus();
        });
    }

    chips.forEach(chip => {
        chip.addEventListener('click', () => {
            chips.forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
        });
    });
})();

/* ── setSearch: called by quick-filter chips ── */
window.setSearch = function(term) {
    const input = document.getElementById('unifiedSearch');
    if (!input) return;
    input.value = term;
    input.dispatchEvent(new Event('input'));
    input.focus();
    const clearBtn = document.getElementById('searchClearBtn');
    if (clearBtn) clearBtn.style.display = 'flex';
};
