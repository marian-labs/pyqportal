/* =====================================================================
   uploadjs.js — Upload page specific scripts for PYQ Portal
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

/* Close on Escape key */
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closePopup();
});

/* ── Upload area: ripple effect on click ── */
const uploadArea = document.getElementById('fileUploadArea');
if (uploadArea) {
    uploadArea.addEventListener('click', function (e) {
        /* Ripple */
        const ripple = document.createElement('span');
        const rect   = this.getBoundingClientRect();
        const size   = Math.max(rect.width, rect.height);
        ripple.style.cssText = `
            position: absolute;
            width: ${size}px;
            height: ${size}px;
            left: ${e.clientX - rect.left - size / 2}px;
            top:  ${e.clientY - rect.top  - size / 2}px;
            background: rgba(59,130,246,0.12);
            border-radius: 50%;
            transform: scale(0);
            animation: rippleAnim 0.6s ease-out forwards;
            pointer-events: none;
        `;
        this.style.position = 'relative';
        this.style.overflow = 'hidden';
        this.appendChild(ripple);
        ripple.addEventListener('animationend', () => ripple.remove());
    });
}

/* Inject ripple keyframe once */
const rippleStyle = document.createElement('style');
rippleStyle.textContent = `
    @keyframes rippleAnim {
        to { transform: scale(2); opacity: 0; }
    }
`;
document.head.appendChild(rippleStyle);

/* ── Submit button: loading spinner on submit ── */
const uploadForm = document.getElementById('uploadForm');
if (uploadForm) {
    uploadForm.addEventListener('submit', function () {
        const btn = this.querySelector('button[type="submit"]');
        if (!btn) return;
        btn.disabled = true;
        btn.innerHTML = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 style="animation: spin 0.8s linear infinite;">
                <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83
                         M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
            </svg>
            Uploading…`;
        /* inject spin keyframe once */
        if (!document.getElementById('spinStyle')) {
            const s = document.createElement('style');
            s.id = 'spinStyle';
            s.textContent = '@keyframes spin { to { transform: rotate(360deg); } }';
            document.head.appendChild(s);
        }
    });
}

/* ── Select fields: subtle flash on change ── */
document.querySelectorAll('.form-control').forEach(el => {
    el.addEventListener('change', function () {
        if (!this.value) return;
        this.style.transition = 'background 0.3s ease';
        this.style.background = 'rgba(59,130,246,0.06)';
        setTimeout(() => { this.style.background = ''; }, 500);
    });
});
