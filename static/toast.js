/**
 * Archivebate Video Browser - Toast Module
 * Obsługa powiadomień Toast.
 */

(function (global) {
  'use strict';

  const context = global.ArchivebateAppContext || { dom: {} };
  const dom = context.dom || {};

  function show(message, type = 'info', existingToast = null, actions = [], options = {}) {
    let toast = existingToast;
    let icon = 'fa-info-circle';
    if (type === 'success') icon = 'fa-circle-check';
    if (type === 'error') icon = 'fa-circle-exclamation';

    if (!toast || !toast.parentNode) {
      toast = document.createElement('div');
      toast.className = 'toast';
      const dialog = Array.from(document.querySelectorAll('[aria-modal="true"]')).find(element => element.getClientRects().length > 0);
      let container = dialog?.querySelector('.toast-container') || (dialog ? null : dom.toastContainer || document.getElementById('toastContainer'));
      if (!container) {
        container = document.createElement('div');
        if (!dialog) container.id = 'toastContainer';
        container.className = 'toast-container';
        (dialog || document.body).appendChild(container);
      }
      container.appendChild(toast);
    }
    clearTimeout(toast._removeTimer);
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-atomic', 'true');
    toast.replaceChildren();
    const iconEl = document.createElement('i');
    iconEl.className = `fa-solid ${icon}`;
    iconEl.style.color = type === 'success' ? 'var(--success)' : type === 'error' ? 'var(--danger)' : 'var(--primary)';
    iconEl.setAttribute('aria-hidden', 'true');
    const textEl = document.createElement('span');
    textEl.textContent = String(message ?? '');
    toast.append(iconEl, document.createTextNode(' '), textEl);
    if (Array.isArray(actions)) {
      actions.forEach(action => {
        if (!action || typeof action.onClick !== 'function') return;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'toast-action';
        button.textContent = String(action.label || 'Wykonaj');
        button.addEventListener('click', () => action.onClick(button));
        toast.appendChild(button);
      });
    }
    toast.style.opacity = '1';
    toast.style.transform = 'none';

    clearTimeout(toast._timer);
    const duration = options.expiresAt != null ? Math.max(0, options.expiresAt - Date.now()) : (options.durationMs ?? 4000);
    toast._timer = setTimeout(() => {
      toast.querySelectorAll('button').forEach(button => { button.disabled = true; });
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      toast.style.transition = 'all 0.3s ease';
      toast._removeTimer = setTimeout(() => toast.remove(), 300);
    }, duration);

    return toast;
  }

  global.ArchivebateToast = { show };
})(typeof window !== 'undefined' ? window : globalThis);
