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

  window.ArchivebateDOM = { escapeHtml, safeUrl };
})();
