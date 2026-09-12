(function (global) {
  'use strict';

  const context = global.ArchivebateAppContext || { dom: {} };
  const dom = context.dom || {};
  let setActiveNavTab;
  let performSearch;

  const SearchAutocomplete = {
    activeIdx: -1,
    currentSuggestions: [],
    debounceTimer: null,
    abortController: null,

    init(dependencies = {}) {
      setActiveNavTab = dependencies.setActiveNavTab;
      performSearch = dependencies.performSearch;

      if (!dom.searchInput || !dom.searchAutocompleteDropdown) return;

      dom.searchInput.addEventListener('input', (e) => {
        const q = e.target.value.trim();
        if (!q) {
          this.hide();
          return;
        }
        clearTimeout(this.debounceTimer);
        this.debounceTimer = setTimeout(() => {
          this.fetchAndRender(q);
        }, 120);
      });

      dom.searchInput.addEventListener('keydown', (e) => {
        if (!this.isOpen()) {
          if (e.key === 'ArrowDown') {
            const q = dom.searchInput.value.trim();
            if (q) this.fetchAndRender(q);
          }
          return;
        }

        if (e.key === 'ArrowDown') {
          e.preventDefault();
          this.moveActive(1);
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          this.moveActive(-1);
        } else if (e.key === 'Enter') {
          if (this.activeIdx >= 0 && this.activeIdx < this.currentSuggestions.length) {
            e.preventDefault();
            this.select(this.currentSuggestions[this.activeIdx]);
          } else {
            this.hide();
          }
        } else if (e.key === 'Escape') {
          e.preventDefault();
          this.hide();
        }
      });

      dom.searchAutocompleteDropdown.addEventListener('click', (e) => {
        const itemEl = e.target.closest('.autocomplete-item');
        if (!itemEl) return;
        const idx = parseInt(itemEl.dataset.index, 10);
        if (!isNaN(idx) && this.currentSuggestions[idx]) {
          this.select(this.currentSuggestions[idx]);
        }
      });

      document.addEventListener('click', (e) => {
        if (!e.target.closest('.search-wrapper')) {
          this.hide();
        }
      });
    },

    isOpen() {
      return dom.searchAutocompleteDropdown && dom.searchAutocompleteDropdown.style.display !== 'none';
    },

    hide() {
      if (dom.searchAutocompleteDropdown) {
        dom.searchAutocompleteDropdown.style.display = 'none';
        dom.searchAutocompleteDropdown.replaceChildren();
      }
      this.activeIdx = -1;
      this.currentSuggestions = [];
    },

    async fetchAndRender(query) {
      if (!query) {
        this.hide();
        return;
      }
      if (this.abortController) this.abortController.abort();
      this.abortController = new AbortController();

      try {
        const res = await fetch(`/api/search/suggestions?q=${encodeURIComponent(query)}&limit=8`, {
          signal: this.abortController.signal
        });
        if (!res.ok) return;
        const data = await res.json();
        const suggestions = data.suggestions || [];
        if (!suggestions.length) {
          this.hide();
          return;
        }
        this.currentSuggestions = suggestions;
        this.activeIdx = -1;
        this.render(query, suggestions);
      } catch (e) {
        if (e.name !== 'AbortError') this.hide();
      }
    },

    render(query, suggestions) {
      const rawClean = query.toLowerCase().replace(/^#/, '');
      dom.searchAutocompleteDropdown.replaceChildren();
      suggestions.forEach((s, idx) => {
        const isTag = s.type === 'tag';
        const isFav = !!s.is_favorite;
        const badgeText = isFav ? 'Ulubiona' : (isTag ? 'Tag' : (s.gender || 'Modelka'));
        const badgeClass = isFav ? 'fav' : (isTag ? 'tag' : '');

        const disp = String(s.display || s.value || '');
        const lowerDisp = disp.toLowerCase();
        const matchIdx = lowerDisp.indexOf(rawClean);
        const item = document.createElement('div');
        item.className = `autocomplete-item ${isFav ? 'is-fav' : ''} ${isTag ? 'is-tag' : ''}`;
        item.dataset.index = String(idx);
        const iconBox = document.createElement('div');
        iconBox.className = 'autocomplete-icon';
        const icon = document.createElement('i');
        icon.className = isTag ? 'fa-solid fa-hashtag' : (isFav ? 'fa-solid fa-star' : 'fa-solid fa-circle-user');
        icon.setAttribute('aria-hidden', 'true');
        iconBox.appendChild(icon);
        const textBox = document.createElement('span');
        textBox.className = 'autocomplete-text';
        if (matchIdx >= 0 && rawClean.length > 0) {
          textBox.append(document.createTextNode(disp.substring(0, matchIdx)));
          const match = document.createElement('span');
          match.className = 'autocomplete-match';
          match.textContent = disp.substring(matchIdx, matchIdx + rawClean.length);
          textBox.append(match, document.createTextNode(disp.substring(matchIdx + rawClean.length)));
        } else {
          textBox.textContent = disp;
        }
        const badge = document.createElement('span');
        badge.className = `autocomplete-badge ${badgeClass}`;
        badge.textContent = String(badgeText || '');
        item.append(iconBox, textBox, badge);
        dom.searchAutocompleteDropdown.appendChild(item);
      });
      dom.searchAutocompleteDropdown.style.display = 'flex';
    },

    moveActive(delta) {
      if (!this.currentSuggestions.length) return;
      const items = dom.searchAutocompleteDropdown.querySelectorAll('.autocomplete-item');
      if (!items.length) return;

      if (this.activeIdx >= 0 && items[this.activeIdx]) {
        items[this.activeIdx].classList.remove('active');
      }

      this.activeIdx += delta;
      if (this.activeIdx < 0) this.activeIdx = items.length - 1;
      if (this.activeIdx >= items.length) this.activeIdx = 0;

      const currentEl = items[this.activeIdx];
      if (currentEl) {
        currentEl.classList.add('active');
        currentEl.scrollIntoView({ block: 'nearest' });
      }
    },

    select(suggestion) {
      if (!suggestion) return;
      this.hide();
      dom.searchInput.value = suggestion.value;
      dom.clearSearchBtn.style.display = 'flex';
      setActiveNavTab(null);
      performSearch(suggestion.value, 1);
    }
  };

  global.ArchivebateSearchAutocomplete = SearchAutocomplete;
})(typeof window !== 'undefined' ? window : globalThis);
