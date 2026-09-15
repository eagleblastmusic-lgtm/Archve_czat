const assert = require('node:assert/strict');
const fs = require('node:fs');

const prefetch = fs.readFileSync('static/video-prefetch.js', 'utf8');
assert.match(prefetch, /lazyThumbObserver\.observe\(img\)/, 'offscreen thumbnails must be observer-driven');
assert.match(prefetch, /global\.lazyThumbObserver = lazyThumbObserver/, 'view changes must be able to disconnect stale thumbnail observers');
assert.match(prefetch, /const warmCount = Math\.min\(Math\.max\(0, count\), 12\)/, 'thumbnail warmup must remain bounded');
assert.match(prefetch, /if \(lazyThumbObserver\)[\s\S]*?observe\(img\)[\s\S]*?return;[\s\S]*?img\.src = src/, 'immediate src assignment must be fallback-only when IntersectionObserver is unavailable');

const perf = fs.readFileSync('static/performance.js', 'utf8');
assert.match(perf, /searchParams\.delete\('initial_items'\)/, 'home feed fast path must request the complete page instead of a 16-card handshake');
assert.match(perf, /catalog_complete === true && data\.page_complete !== false[\s\S]*?data\.complete = true/, 'published complete catalog pages must not open a redundant SSE round-trip');
assert.match(perf, /archivebate-icon-fallback/, 'fresh browsers need a local icon fallback when Font Awesome CDN is unavailable');
assert.match(perf, /let budget = 16/, 'only a bounded first-screen thumbnail set may be promoted to eager loading');

const stats = fs.readFileSync('static/home-stats.js', 'utf8');
assert.match(stats, /Indeksowanie trwa/, 'incomplete catalog must clearly say indexing is still running');
assert.match(stats, /Gotowe/, 'complete catalog must clearly say indexing has finished');
assert.match(stats, /ostatni zapis/, 'stale incomplete indexing should expose last-write age');

console.log('PASS: home requests a complete page, keeps thumbnail boost bounded, has local icon fallback and exposes catalog completion state');
