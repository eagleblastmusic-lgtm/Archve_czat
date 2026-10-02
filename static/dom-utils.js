(() => {
  'use strict';

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }

  function safeUrl(value) {
    const raw = String(value || '').trim();
    if (/^https?:\/\//i.test(raw) || /^\/(?!\/)/.test(raw) || raw === '#') return raw;
    return '';
  }

  function manageDialogFocus(dialog, initialFocus, onClose) {
    const previousFocus = document.activeElement;
    const backgrounds = Array.from(document.body.children).filter(element => !element.contains(dialog));
    const priorInert = backgrounds.map(element => element.inert);
    backgrounds.forEach(element => { element.inert = true; });
    const controls = () => Array.from(dialog.querySelectorAll('button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
      .filter(element => !element.disabled && !element.hidden && element.offsetParent !== null);
    const focus = () => (initialFocus?.isConnected ? initialFocus : controls()[0] || dialog).focus?.();
    const onKey = event => {
      if (event.key === 'Escape' && onClose) { event.preventDefault(); event.stopPropagation(); onClose(); return; }
      if (event.key !== 'Tab') return;
      const items = controls();
      if (!items.length) { event.preventDefault(); dialog.focus?.(); return; }
      const index = items.indexOf(document.activeElement);
      const next = event.shiftKey ? (index <= 0 ? items.length - 1 : index - 1) : (index + 1) % items.length;
      event.preventDefault(); items[next].focus();
    };
    const onFocus = event => { if (!dialog.contains(event.target)) focus(); };
    dialog.addEventListener('keydown', onKey);
    document.addEventListener('focusin', onFocus);
    focus();
    return () => {
      dialog.removeEventListener('keydown', onKey);
      document.removeEventListener('focusin', onFocus);
      backgrounds.forEach((element, index) => { element.inert = priorInert[index]; });
      if (previousFocus?.isConnected) previousFocus.focus?.();
    };
  }

  window.ArchivebateDOM = { escapeHtml, safeUrl, manageDialogFocus };
})();
