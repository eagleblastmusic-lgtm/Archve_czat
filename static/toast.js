/**
 * Archivebate Video Browser - Toast Module
 * Obsługa powiadomień Toast.
 */

(function (global) {
  'use strict';

  const context = global.ArchivebateAppContext || { dom: {} };
  const dom = context.dom || {};

  function show(message, type = 'info', existingToast = null) {
    let toast = existingToast;
    let icon = 'fa-info-circle';
    if (type === 'success') icon = 'fa-circle-check';
    if (type === 'error') icon = 'fa-circle-exclamation';

    if (!toast || !toast.parentNode) {
      toast = document.createElement('div');
      toast.className = 'toast';
      dom.toastContainer.appendChild(toast);
    }
    toast.innerHTML = `<i class="fa-solid ${icon}" style="color: ${type === 'success' ? 'var(--success)' : type === 'error' ? 'var(--danger)' : 'var(--primary)'}"></i> <span>${message}</span>`;
    toast.style.opacity = '1';
    toast.style.transform = 'none';

    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      toast.style.transition = 'all 0.3s ease';
      setTimeout(() => toast.remove(), 300);
    }, 4000);

    return toast;
  }

  global.ArchivebateToast = { show };
})(typeof window !== 'undefined' ? window : globalThis);
