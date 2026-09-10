(function (global) {
  'use strict';

  const context = global.ArchivebateAppContext || { state: {}, dom: {} };
  const state = context.state || {};
  const dom = context.dom || {};

  let loadHomeVideos;
  let performSearch;
  let loadModelVideos;
  let loadFavorites;
  let loadHistory;
  let loadFollowing;

  function init(dependencies = {}) {
    loadHomeVideos = dependencies.loadHomeVideos;
    performSearch = dependencies.performSearch;
    loadModelVideos = dependencies.loadModelVideos;
    loadFavorites = dependencies.loadFavorites;
    loadHistory = dependencies.loadHistory;
    loadFollowing = dependencies.loadFollowing;
  }

  function changePage(newPage) {
    state.currentPage = newPage;
    window.scrollTo({ top: 0, behavior: 'smooth' });

    if (state.mode === 'home') {
      loadHomeVideos(newPage);
    } else if (state.mode === 'search') {
      performSearch(state.currentQuery, newPage);
    } else if (state.mode === 'model') {
      loadModelVideos(state.currentModel, newPage);
    } else if (state.mode === 'favorites') {
      loadFavorites(newPage);
    } else if (state.mode === 'history') {
      loadHistory(newPage);
    } else if (state.mode === 'following') {
      loadFollowing(newPage);
    }
  }

  function render() {
    const current = state.currentPage;
    const maxP = state.lastPage || 1;

    function renderControls(prevBtn, nextBtn, lastBtn, lastNum, jumpInput, listEl) {
      if (prevBtn) {
        prevBtn.disabled = current <= 1;
        prevBtn.onclick = () => { if (current > 1) changePage(current - 1); };
      }
      if (nextBtn) {
        nextBtn.disabled = current >= maxP;
        nextBtn.onclick = () => { if (current < maxP) changePage(current + 1); };
      }
      if (lastBtn && lastNum) {
        lastNum.innerText = maxP.toLocaleString('pl-PL');
        lastBtn.disabled = current >= maxP;
        lastBtn.onclick = () => changePage(maxP);
      }
      if (jumpInput) {
        jumpInput.max = maxP;
        jumpInput.value = current;
      }
      if (!listEl) return;
      listEl.innerHTML = '';

      const startPage = Math.max(1, current - 2);
      const endPage = Math.min(maxP, startPage + 4);

      if (startPage > 1) {
        addPageButton(1, listEl);
        if (startPage > 2) {
          const dots = document.createElement('span');
          dots.className = 'page-num-dots';
          dots.innerText = '...';
          listEl.appendChild(dots);
        }
      }

      for (let p = startPage; p <= endPage; p++) {
        addPageButton(p, listEl);
      }

      if (endPage < maxP) {
        const dotsEnd = document.createElement('span');
        dotsEnd.className = 'page-num-dots';
        dotsEnd.innerText = '...';
        listEl.appendChild(dotsEnd);
        addPageButton(maxP, listEl);
      }
    }

    function addPageButton(pageNumber, listEl) {
      const btn = document.createElement('button');
      btn.className = 'page-num-btn';
      if (pageNumber === current) btn.classList.add('active');
      btn.innerText = pageNumber.toLocaleString('pl-PL');
      btn.addEventListener('click', () => changePage(pageNumber));
      listEl.appendChild(btn);
    }

    if (dom.paginationSection) {
      dom.paginationSection.style.display = 'flex';
    }
    renderControls(dom.prevPageBtn, dom.nextPageBtn, dom.lastPageBtn, dom.lastPageNumber, dom.pageJumpInput, dom.pageNumbersList);

    if (dom.paginationSectionTop) {
      dom.paginationSectionTop.style.display = 'flex';
    }
    renderControls(dom.prevPageBtnTop, dom.nextPageBtnTop, dom.lastPageBtnTop, dom.lastPageNumberTop, dom.pageJumpInputTop, dom.pageNumbersListTop);
  }

  global.ArchivebatePagination = {
    init,
    changePage,
    render
  };
})(typeof window !== 'undefined' ? window : globalThis);
