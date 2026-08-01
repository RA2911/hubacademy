/**
 * Hub Academy - Main JavaScript
 */

document.addEventListener('DOMContentLoaded', function() {
    initFlashMessages();
});

/**
 * Modal Management
 */
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    }
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.remove('active');
        document.body.style.overflow = '';
    }
}

// Close modal on escape key
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        const activeModal = document.querySelector('.modal.active');
        if (activeModal) {
            activeModal.classList.remove('active');
            document.body.style.overflow = '';
        }
    }
});

/**
 * Flash Messages
 */
function initFlashMessages() {
    const flashContainer = document.getElementById('flash-container');
    if (!flashContainer) return;

    const messages = flashContainer.querySelectorAll('.flash-message');
    messages.forEach(function(message) {
        const dismissTime = parseInt(message.dataset.autoDismiss) || 5000;
        setTimeout(function() {
            message.style.animation = 'flashSlide 0.3s ease reverse';
            setTimeout(function() {
                message.remove();
            }, 300);
        }, dismissTime);
    });
}

/**
 * Confirm Dialog
 */
function confirmAction(message, callback) {
    if (confirm(message)) {
        callback();
    }
}

/**
 * AJAX Helper
 */
function ajax(url, options = {}) {
    const defaults = {
        method: 'GET',
        headers: {
            'X-Requested-With': 'XMLHttpRequest',
        }
    };

    // Add CSRF token if available
    const csrfToken = document.querySelector('meta[name="csrf-token"]');
    if (csrfToken) {
        defaults.headers['X-CSRFToken'] = csrfToken.content;
    }

    const config = { ...defaults, ...options };
    config.headers = { ...defaults.headers, ...options.headers };

    return fetch(url, config)
        .then(response => {
            if (!response.ok) {
                throw new Error('Network response was not ok');
            }
            return response.json();
        });
}

/**
 * Show notification
 */
function showNotification(message, type = 'info') {
    let container = document.getElementById('flash-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'flash-container';
        container.className = 'flash-container';
        document.body.appendChild(container);
    }

    const iconMap = {
        success: 'fas fa-check-circle',
        error: 'fas fa-exclamation-circle',
        warning: 'fas fa-exclamation-triangle',
        info: 'fas fa-info-circle'
    };

    const notification = document.createElement('div');
    notification.className = `flash-message flash-${type}`;
    notification.innerHTML = `
        <div class="flash-content">
            <i class="${iconMap[type]}"></i>
            <span>${message}</span>
        </div>
        <button class="flash-close" onclick="this.parentElement.remove()">
            <i class="fas fa-times"></i>
        </button>
    `;

    container.appendChild(notification);

    setTimeout(function() {
        notification.style.animation = 'flashSlide 0.3s ease reverse';
        setTimeout(function() {
            notification.remove();
        }, 300);
    }, 5000);
}

/**
 * Format file size
 */
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

/* ===== VIDEO SEEK CONTROLS (fast-forward / rewind) =====
 * Powers the skip buttons under each player and adds keyboard seeking so
 * learners can move within a video regardless of the native scrub bar. */
function skipVideo(target, seconds) {
    const v = (typeof target === 'string') ? document.getElementById(target) : target;
    if (!v) return;
    const max = isFinite(v.duration) ? v.duration : Number.MAX_SAFE_INTEGER;
    v.currentTime = Math.min(max, Math.max(0, v.currentTime + seconds));
}
window.skipVideo = skipVideo;

// Arrow keys seek ±10s and Space toggles play when a <video> has focus.
document.addEventListener('keydown', function (e) {
    const v = document.activeElement;
    if (!v || v.tagName !== 'VIDEO') return;
    if (e.key === 'ArrowRight') { skipVideo(v, 10); e.preventDefault(); }
    else if (e.key === 'ArrowLeft') { skipVideo(v, -10); e.preventDefault(); }
    else if (e.key === ' ' || e.key === 'Spacebar') { v.paused ? v.play() : v.pause(); e.preventDefault(); }
});

/* ===== ADMIN SIDEBAR TOGGLE (hamburger for narrow screens) =====
 * The admin sidebar is hidden below 1024px (CSS) with no way to reopen it.
 * We inject a hamburger button + backdrop here so every admin page gets a
 * working mobile menu without editing all 13 admin templates. */
document.addEventListener('DOMContentLoaded', function () {
    var layout = document.querySelector('.admin-layout');
    var sidebar = layout && layout.querySelector('.sidebar');
    if (!sidebar) return;

    var toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'admin-menu-toggle';
    toggle.setAttribute('aria-label', 'Toggle navigation menu');
    toggle.innerHTML = '<i class="fas fa-bars"></i>';

    var overlay = document.createElement('div');
    overlay.className = 'admin-sidebar-overlay';

    document.body.appendChild(toggle);
    document.body.appendChild(overlay);

    function setOpen(open) {
        sidebar.classList.toggle('open', open);
        overlay.classList.toggle('show', open);
        toggle.innerHTML = open ? '<i class="fas fa-times"></i>' : '<i class="fas fa-bars"></i>';
    }

    toggle.addEventListener('click', function () {
        setOpen(!sidebar.classList.contains('open'));
    });
    overlay.addEventListener('click', function () { setOpen(false); });
    sidebar.querySelectorAll('a').forEach(function (a) {
        a.addEventListener('click', function () { setOpen(false); });
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') setOpen(false);
    });
    window.addEventListener('resize', function () {
        if (window.innerWidth > 1024) setOpen(false);
    });
});
