const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const index = fs.readFileSync('static/index.html', 'utf8');
const watch = fs.readFileSync('static/watch.html', 'utf8');
const playerCore = fs.readFileSync('static/player-core.js', 'utf8');
const modalControls = fs.readFileSync('static/modal-player-controls.js', 'utf8');
const account = fs.readFileSync('static/account.js', 'utf8');

for (const [name, source] of [['index.html', index], ['watch.html', watch]]) {
  assert.match(source, /role="slider"[^>]*tabindex="0"/, `${name}: oś czasu musi być klawiaturowym sliderem`);
  assert.match(source, /aria-label="Pozycja filmu"/, `${name}: oś czasu musi mieć etykietę`);
  assert.match(source, /aria-label="Głośność"/, `${name}: suwak głośności musi mieć etykietę`);
  assert.match(source, /aria-label="Pełny ekran \(F\)"/, `${name}: fullscreen musi mieć etykietę`);
  assert.match(source, /aria-label="Włącz obraz w obrazie"/, `${name}: PiP musi mieć etykietę`);
}

for (const helper of [
  'formatTime',
  'parseDurationToSeconds',
  'updateVolumeIcon',
  'setupVolumeControls',
  'setupSpeedToggle',
  'setupFullscreenToggle',
  'setupIdleTimer'
]) {
  assert.match(playerCore, new RegExp(`function ${helper}\\b`), `player-core musi eksportować helper ${helper}`);
}
assert.match(modalControls, /playPrevAuthorVideo/);
assert.match(modalControls, /playNextAuthorVideo/);
assert.match(modalControls, /addEventListener\('keydown'/);
assert.match(account, /paginationSectionTop\) dom\.paginationSectionTop\.style\.display = 'none'/, 'panel konta nie może pokazywać paginacji feedu');
assert.match(index, /id="jobsPanelTitle"/, 'panel konta powinien pokazywać centrum zadań');
assert.match(account, /\/api\/jobs/, 'centrum zadań powinno używać jednego kontraktu statusu');

function target() {
  const listeners = new Map();
  return {
    value: '1',
    innerText: '',
    innerHTML: '',
    attrs: {},
    classList: { add() {}, remove() {} },
    addEventListener(name, fn) { listeners.set(name, fn); },
    removeEventListener(name) { listeners.delete(name); },
    dispatch(name, event = {}) { listeners.get(name)?.({ target: this, ...event }); },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) { return this.attrs[name]; }
  };
}

const context = {
  window: {},
  setTimeout,
  clearTimeout,
  requestAnimationFrame: fn => setTimeout(fn, 0),
  performance: { now: () => 0 }
};
vm.createContext(context);
vm.runInContext(playerCore, context);
const core = context.window.ArchivebatePlayerCore;
const video = target();
Object.assign(video, { volume: 1, muted: false, playbackRate: 1, paused: true, ended: false });
const slider = target();
const volumeButton = target();
core.setupVolumeControls(video, slider, volumeButton);
slider.value = '0.25';
slider.dispatch('input');
assert.equal(video.volume, 0.25);
volumeButton.dispatch('click');
assert.equal(video.muted, true);
volumeButton.dispatch('click');
assert.equal(video.muted, false);
assert.equal(video.volume, 0.25);

const speedButton = target();
core.setupSpeedToggle(video, speedButton);
speedButton.dispatch('click');
assert.equal(video.playbackRate, 1.25);
assert.match(speedButton.getAttribute('aria-label'), /1\.25x/);

console.log('PASS: player controls have working shared helpers, keyboard timeline semantics and accessible labels');
