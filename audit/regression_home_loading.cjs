const assert = require('node:assert/strict');
const fs = require('node:fs');

const prefetch = fs.readFileSync('static/video-prefetch.js', 'utf8');
assert.match(prefetch, /lazyThumbObserver\.observe\(img\)/, 'offscreen thumbnails must be observer-driven');
assert.match(prefetch, /global\.lazyThumbObserver = lazyThumbObserver/, 'view changes must be able to disconnect stale thumbnail observers');
assert.match(prefetch, /const warmCount = Math\.min\(Math\.max\(0, count\), 12\)/, 'thumbnail warmup must remain bounded');
assert.match(prefetch, /if \(lazyThumbObserver\)[\s\S]*?observe\(img\)[\s\S]*?return;[\s\S]*?img\.src = src/, 'immediate src assignment must be fallback-only when IntersectionObserver is unavailable');

const runtime = fs.readFileSync('runtime_app.py', 'utf8');
const resilience = fs.readFileSync('static/lazy-thumbnail-resilience.js', 'utf8');
assert.match(runtime, /lazy-thumbnail-resilience\.js\?v=1/, 'release runtime must inject thumbnail scroll resilience');
assert.match(runtime, /"lazy_thumbnail_scroll_resilience": True/, 'runtime marker must expose thumbnail resilience');
assert.match(resilience, /\.thumbnail-img\[data-src\]/, 'resilience layer must target only pending lazy thumbnails');
assert.match(resilience, /getBoundingClientRect\(\)/, 'thumbnail rescue must remain viewport-bounded');
assert.match(resilience, /PRELOAD_MARGIN_PX = 900/, 'thumbnail rescue must preload only a bounded viewport margin');
assert.match(resilience, /requestAnimationFrame/, 'scroll recovery must be frame-throttled');
assert.match(resilience, /addEventListener\?\.\('scroll'/, 'scrolling must trigger a lazy-thumbnail recovery scan');
assert.match(resilience, /MutationObserver/, 'dynamically appended feed cards must also be recovered');
assert.doesNotMatch(resilience, /querySelectorAll\?\.\('\.thumbnail-img'\)(?!\[data-src\])/, 'resilience must not eagerly promote every thumbnail');

const stats = fs.readFileSync('static/home-stats.js', 'utf8');
assert.match(stats, /Indeksowanie trwa/, 'incomplete catalog must clearly say indexing is still running');
assert.match(stats, /Gotowe/, 'complete catalog must clearly say indexing has finished');
assert.match(stats, /ostatni zapis/, 'stale incomplete indexing should expose last-write age');

console.log('PASS: home first-paint thumbnails stay lazy, scroll recovery is resilient, and catalog completion state is explicit');
