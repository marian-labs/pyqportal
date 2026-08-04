/* =====================================================================
   aboutjs.js — About page specific scripts for PYQ Portal
   ===================================================================== */

/* ── Scroll reveal ── */
const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach(e => {
        if (e.isIntersecting) e.target.classList.add('visible');
    });
}, { threshold: 0.12 });

document.querySelectorAll('.reveal, .reveal-left, .reveal-right').forEach(el => {
    revealObserver.observe(el);
});

/* ── 3D card tilt on hover ── */
document.querySelectorAll('.dev-card').forEach(card => {
    card.addEventListener('mousemove', (e) => {
        const rect = card.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width - 0.5;
        const y = (e.clientY - rect.top)  / rect.height - 0.5;
        card.style.transform = `translateY(-6px) rotateX(${-y * 5}deg) rotateY(${x * 5}deg)`;
    });
    card.addEventListener('mouseleave', () => {
        card.style.transform = '';
    });
});

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
