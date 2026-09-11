const assert = require('node:assert/strict');
const fs = require('node:fs');

const blockedSource = fs.readFileSync('static/blocked-models.js', 'utf8');

assert.doesNotMatch(
  blockedSource,
  /loadHomeVideos\(state\.currentPage\s*,\s*true\)/,
  'blocking an author must not force-reload the current page'
);
assert.match(
  blockedSource,
  /state\.videos\s*=\s*state\.videos\.filter/,
  'blocking an author must prune the current client-side video list'
);
assert.match(
  blockedSource,
  /state\.gridCardMap\.delete\(key\)/,
  'blocking an author must remove stale cards from the client grid map'
);
assert.match(
  blockedSource,
  /clientBlockedAuthors\(\)\.add\(norm\)/,
  'blocking an author must mark the optimistic client-side block'
);
assert.match(
  blockedSource,
  /setTimeout\(\(\) => c\.remove\(\), 180\)/,
  'blocked cards should disappear locally so CSS Grid can close the gap'
);

console.log('PASS: blocking an author compacts the current grid without reloading the page');
