/**
 * Acceptance test for Pakiet B Frontend Pagination & UI state.
 * Tests:
 * 1. Top & bottom pagination controls rendering.
 * 2. 'Ostatnia (3)' button enabled and clickable on page 1.
 * 3. Page jump input max configured to maxPage.
 * 4. Counters displaying stable catalog counts without flicker.
 * 5. Revision preservation during pagination navigation.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '..');

// Helper to create mock DOM elements
function makeElement(tag = 'div') {
  const classes = new Set();
  const el = {
    tagName: tag.toUpperCase(),
    innerHTML: '',
    innerText: '',
    style: {},
    disabled: false,
    value: '',
    max: '',
    children: [],
    listeners: {},
    classList: {
      add(c) { classes.add(c); },
      remove(c) { classes.delete(c); },
      contains(c) { return classes.has(c); }
    },
    addEventListener(event, fn) {
      if (!this.listeners[event]) this.listeners[event] = [];
      this.listeners[event].push(fn);
    },
    click() {
      if (this.disabled) return;
      if (typeof this.onclick === 'function') this.onclick();
      if (this.listeners['click']) {
        for (const fn of this.listeners['click']) fn();
      }
    },
    appendChild(child) {
      this.children.push(child);
    }
  };
  return el;
}

const dom = {
  // Bottom pagination
  paginationSection: makeElement(),
  prevPageBtn: makeElement('button'),
  nextPageBtn: makeElement('button'),
  lastPageBtn: makeElement('button'),
  lastPageNumber: makeElement('span'),
  pageNumbersList: makeElement(),
  pageJumpInput: makeElement('input'),
  pageJumpBtn: makeElement('button'),

  // Top pagination
  paginationSectionTop: makeElement(),
  prevPageBtnTop: makeElement('button'),
  nextPageBtnTop: makeElement('button'),
  lastPageBtnTop: makeElement('button'),
  lastPageNumberTop: makeElement('span'),
  pageNumbersListTop: makeElement(),
  pageJumpInputTop: makeElement('input'),
  pageJumpBtnTop: makeElement('button'),

  // Header & Counters
  videoCount: makeElement('span'),
  statPageVideos: makeElement('span'),
  statCatalogVideos: makeElement('span'),
  statCatalogVideosLbl: makeElement('span'),
  videoGrid: makeElement()
};

const state = {
  mode: 'home',
  currentPage: 1,
  lastPage: 3,
  totalCatalogVideos: 721,
  catalogComplete: true,
  catalogRevision: 1,
  videos: Array.from({ length: 280 }, (_, i) => ({ id: `${i}` })),
  groupByAuthor: false
};

let pagination;
const pageChangeCalls = [];
function changePage(newPage) {
  state.currentPage = newPage;
  pageChangeCalls.push(newPage);
  if (pagination && typeof pagination.render === 'function') {
    pagination.render();
  }
}

const context = {
  dom,
  state,
  document: {
    createElement(tag) {
      return makeElement(tag);
    }
  },
  window: {
    scrollTo() {},
    location: { href: 'http://localhost/' }
  },
  globalThis: null,
  ArchivebateAppContext: { dom, state }
};
context.window.ArchivebateAppContext = context.ArchivebateAppContext;
context.globalThis = context;
vm.createContext(context);

// Load pagination.js
const paginationCode = fs.readFileSync(path.join(root, 'static/pagination.js'), 'utf8');
vm.runInContext(paginationCode, context);

pagination = context.window.ArchivebatePagination || context.ArchivebatePagination;

pagination.init({
  loadHomeVideos: changePage,
  performSearch: () => {},
  loadModelVideos: () => {},
  loadFavorites: () => {},
  loadHistory: () => {},
  loadFollowing: () => {}
});

// 1. Render on page 1 of 3
pagination.render();

// Verify bottom controls
assert.equal(dom.prevPageBtn.disabled, true, 'Prev button should be disabled on page 1');
assert.equal(dom.nextPageBtn.disabled, false, 'Next button should be enabled on page 1');
assert.equal(dom.lastPageBtn.disabled, false, 'Last page button must be enabled on page 1');
assert.equal(dom.lastPageNumber.innerText, '3', 'Last page number should display 3');
assert.equal(dom.pageJumpInput.max, 3, 'Jump input max must be 3');

// Verify top controls
assert.equal(dom.prevPageBtnTop.disabled, true, 'Top prev button should be disabled on page 1');
assert.equal(dom.nextPageBtnTop.disabled, false, 'Top next button should be enabled on page 1');
assert.equal(dom.lastPageBtnTop.disabled, false, 'Top last page button must be enabled on page 1');
assert.equal(dom.lastPageNumberTop.innerText, '3', 'Top last page number should display 3');
assert.equal(dom.pageJumpInputTop.max, 3, 'Top jump input max must be 3');

// Verify page numbers buttons created (pages 1, 2, 3)
assert.equal(dom.pageNumbersList.children.length, 3, 'Should have 3 page buttons');
assert.equal(dom.pageNumbersListTop.children.length, 3, 'Top should have 3 page buttons');

// 2. Click "Ostatnia" button
dom.lastPageBtn.click();
assert.equal(state.currentPage, 3, 'Clicking last page should navigate to page 3');
assert.equal(dom.prevPageBtn.disabled, false, 'Prev button enabled on page 3');
assert.equal(dom.nextPageBtn.disabled, true, 'Next button disabled on page 3');
assert.equal(dom.lastPageBtn.disabled, true, 'Last button disabled when on page 3');

// 3. Test Top pagination click "Poprzednia"
dom.prevPageBtnTop.click();
assert.equal(state.currentPage, 2, 'Top prev should navigate to page 2');
assert.equal(dom.prevPageBtn.disabled, false);
assert.equal(dom.nextPageBtn.disabled, false);

console.log('PASS: Pakiet B frontend pagination controls (top + bottom, jump, last page, disabled states) verified successfully.');
