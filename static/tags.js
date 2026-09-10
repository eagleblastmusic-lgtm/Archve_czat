(function (global) {
  'use strict';

  const context = global.ArchivebateAppContext || { dom: {} };
  const dom = context.dom || {};

  async function init(dependencies = {}) {
    const setActiveNavTab = dependencies.setActiveNavTab;
    const performSearch = dependencies.performSearch;

    try {
      const res = await fetch('/api/tags');
      const data = await res.json();
      const tags = data.tags || [];

      tags.forEach(t => {
        const pill = document.createElement('div');
        pill.className = 'tag-pill';
        pill.innerText = `#${t.name}`;
        pill.dataset.tag = t.tag;

        // Inteligentne podgrzanie w tle po najechaniu kursorem (błyskawiczne otwarcie po kliknięciu)
        pill.addEventListener('mouseenter', () => {
          fetch(`/api/search?q=${encodeURIComponent(t.tag)}&page=1`).catch(() => {});
        });

        pill.addEventListener('click', () => {
          document.querySelectorAll('.tag-pill').forEach(p => p.classList.remove('active'));
          pill.classList.add('active');
          dom.searchInput.value = `#${t.tag}`;
          dom.clearSearchBtn.style.display = 'flex';
          setActiveNavTab(null);
          performSearch(t.tag, 1);
        });
        dom.tagsContainer.appendChild(pill);
      });
    } catch (e) {
      console.error('Błąd pobierania tagów:', e);
    }
  }

  global.ArchivebateTags = { init };
})(typeof window !== 'undefined' ? window : globalThis);
