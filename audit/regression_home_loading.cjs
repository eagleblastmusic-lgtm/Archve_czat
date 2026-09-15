const assert = require('node:assert/strict');
const fs = require('node:fs');

const prefetch = fs.readFileSync('static/video-prefetch.js', 'utf8');
assert.match(prefetch, /lazyThumbObserver\.observe\(img\)/, 'offscreen thumbnails must be observer-driven');
assert.match(prefetch, /global\.lazyThumbObserver = lazyThumbObserver/, 'view changes must be able to disconnect stale thumbnail observers');
assert.match(prefetch, /const warmCount = Math\.min\(Math\.max\(0, count\), 12\)/, 'thumbnail warmup must remain bounded');
assert.match(prefetch, /if \(lazyThumbObserver\)[\s\S]*?observe\(img\)[\s\S]*?return;[\s\S]*?img\.src = src/, 'immediate src assignment must be fallback-only when IntersectionObserver is unavailable');

const stats = fs.readFileSync('static/home-stats.js', 'utf8');
assert.match(stats, /Indeksowanie trwa/, 'incomplete catalog must clearly say indexing is still running');
assert.match(stats, /Gotowe/, 'complete catalog must clearly say indexing has finished');
assert.match(stats, /ostatni zapis/, 'stale incomplete indexing should expose last-write age');

console.log('PASS: home first-paint thumbnails stay lazy and catalog completion state is explicit');
