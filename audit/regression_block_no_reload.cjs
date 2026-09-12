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
  /data\s*&&\s*data\.success[\s\S]*?pruneBlockedAuthorFromClientState\(norm\)/,
  'blocking an author may prune the current client-side list only after API confirmation'
);
assert.match(
  blockedSource,
  /state\.gridCardMap\.delete\(key\)/,
  'blocking an author must remove stale cards from the client grid map'
);
assert.match(
  blockedSource,
  /clientBlockedAuthors\(\)\.add\(norm\)/,
  'blocking an author must mark the confirmed client-side block'
);
assert.match(
  blockedSource,
  /pendingBlocks\.add\(norm\)/,
  'duplicate block requests must be serialized while the API is pending'
);

console.log('PASS: blocking an author waits for API confirmation, then compacts the current grid without reloading');
