from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected exactly one match, got {count}')
    p.write_text(text.replace(old, new, 1), encoding='utf-8')


# 1) First paint: fewer heavy cards synchronously, then deterministic chunks.
replace_once(
    'static/video-grid.js',
    "    const INITIAL_BATCH = 32;\n    const CHUNK_SIZE = 32;",
    "    // Keep the first synchronous paint small. A card wires multiple controls,\n"
    "    // hover handlers and media nodes, so creating all 280 at once makes a\n"
    "    // page transition feel much slower than the API response itself.\n"
    "    const INITIAL_BATCH = 16;\n    const CHUNK_SIZE = 24;",
)

# 2) Do not make a dozen poster requests eager/high-priority at the same time.
replace_once(
    'static/video-card.js',
    "    const eagerThumb = idx < 12;\n    const thumbLoadAttrs = eagerThumb\n      ? `src=\"${displayPoster}\" loading=\"eager\" fetchpriority=\"${idx < 6 ? 'high' : 'auto'}\"`",
    "    const eagerThumb = idx < 8;\n    const thumbLoadAttrs = eagerThumb\n      ? `src=\"${displayPoster}\" loading=\"eager\" fetchpriority=\"${idx < 4 ? 'high' : 'auto'}\"`",
)

# 3) Replace the no-op home prefetch with a bounded, revision-aware LRU page cache.
replace_once(
    'static/video-views.js',
    "  function prefetchNextPage() {\n    // Home is filled via its SSE subscriber.\n  }",
    r'''  const HOME_PAGE_CACHE_LIMIT = 3;
  const HOME_PAGE_CACHE_TTL_MS = 90_000;
  const homePageCache = new Map();
  const homePagePrefetchInflight = new Map();

  function homePageCacheKey(page, specKey) {
    return `${specKey}|page:${Number(page) || 1}`;
  }

  function rememberHomePage(page, specKey, data) {
    if (!data || !Array.isArray(data.videos || data.items)) return;
    const key = homePageCacheKey(page, specKey);
    const entry = {
      data: { ...data, videos: data.videos || data.items || [], items: data.items || data.videos || [] },
      revision: Number(data.catalog_revision !== undefined ? data.catalog_revision : data.revision),
      storedAt: Date.now()
    };
    homePageCache.delete(key);
    homePageCache.set(key, entry);
    while (homePageCache.size > HOME_PAGE_CACHE_LIMIT) {
      homePageCache.delete(homePageCache.keys().next().value);
    }
  }

  function getCachedHomePage(page, specKey) {
    const key = homePageCacheKey(page, specKey);
    const entry = homePageCache.get(key);
    if (!entry) return null;
    if (Date.now() - entry.storedAt > HOME_PAGE_CACHE_TTL_MS) {
      homePageCache.delete(key);
      return null;
    }
    const activeRevision = Number(state.catalogRevision);
    if (Number.isFinite(activeRevision) && activeRevision > 0 && Number.isFinite(entry.revision) && entry.revision !== activeRevision) {
      homePageCache.delete(key);
      return null;
    }
    homePageCache.delete(key);
    homePageCache.set(key, entry);
    return entry.data;
  }

  function warmPageThumbnails(data, count = 24) {
    const videos = data?.videos || data?.items || [];
    if (!Array.isArray(videos) || videos.length === 0) return;
    const urls = videos.slice(0, count).map(thumbnailUrlForVideo).filter(Boolean);
    if (!urls.length) return;
    perf().prefetchUrls(urls, { concurrency: 4 }).catch(() => {});
  }

  function prefetchHomePage(page, specKey, revision) {
    const target = Number(page) || 1;
    const maxPage = Math.max(1, Number(state.lastPage) || 1);
    if (target < 1 || target > maxPage) return Promise.resolve(null);
    if (getCachedHomePage(target, specKey)) return Promise.resolve(null);

    const key = homePageCacheKey(target, specKey);
    if (homePagePrefetchInflight.has(key)) return homePagePrefetchInflight.get(key);

    const src = encodeURIComponent(state.sourceFilter || 'all');
    const af = encodeURIComponent(state.authorFilter || 'all');
    const grp = state.groupByAuthor ? '1' : '0';
    const revParam = Number(revision) > 0 ? `&revision=${encodeURIComponent(revision)}` : '';
    const request = api().getJSON(
      `/api/feed?page=${target}&source=${src}&author_filter=${af}&group_authors=${grp}${revParam}`,
      { timeoutMs: 12000 }
    ).then(data => {
      rememberHomePage(target, specKey, data);
      warmPageThumbnails(data, 24);
      return data;
    }).catch(() => null).finally(() => {
      homePagePrefetchInflight.delete(key);
    });
    homePagePrefetchInflight.set(key, request);
    return request;
  }

  function prefetchNextPage() {
    if (state.mode !== 'home') return;
    const current = Math.max(1, Number(state.currentPage) || 1);
    const src = encodeURIComponent(state.sourceFilter || 'all');
    const af = encodeURIComponent(state.authorFilter || 'all');
    const grp = state.groupByAuthor ? '1' : '0';
    const specKey = `${src}|${af}|${grp}`;
    prefetchHomePage(current + 1, specKey, state.catalogRevision);
  }''',
)

replace_once(
    'static/video-views.js',
    "    const specKey = `${src}|${af}|${grp}`;\n\n    const hasExistingCards = Boolean(",
    "    const specKey = `${src}|${af}|${grp}`;\n    const cachedPage = !force ? getCachedHomePage(page, specKey) : null;\n    let renderedFromPageCache = false;\n\n    const hasExistingCards = Boolean(",
)

replace_once(
    'static/video-views.js',
    '''    if (!isSamePageRefresh) {
      state.videos = [];
      showSkeletons();
      state.gridCardMap = new Map();
      state.lastAppliedFeedRevision = -1;
      state.lastAppliedFeedUpdatedAt = 0;
      state.lastAppliedFeedVideoCount = -1;
      state.lastAppliedVideosCount = 0;
    } else {
      setFeedRefreshingIndicator(true);
    }''',
    '''    if (!isSamePageRefresh) {
      if (cachedPage) {
        state.videos = cachedPage.videos || cachedPage.items || [];
        state.feedSpecKey = specKey;
        state.feedSnapshotId = cachedPage.snapshot_id || null;
        state.catalogRevision = cachedPage.catalog_revision !== undefined ? cachedPage.catalog_revision : cachedPage.revision;
        state.lastPage = Math.max(1, Number(cachedPage.page_count || cachedPage.last_page) || 1);
        state.totalCatalogVideos = Number(cachedPage.video_count !== undefined ? cachedPage.video_count : cachedPage.total_videos) || 0;
        state.catalogComplete = !!cachedPage.catalog_complete;
        renderVideoGrid(state.videos);
        scheduleThumbnailWarmup(state.videos, 8, 24);
        renderedFromPageCache = true;
        if (dom.pageJumpInput) dom.pageJumpInput.value = page;
        if (dom.pageJumpInputTop) dom.pageJumpInputTop.value = page;
        if (dom.videoCount) dom.videoCount.innerText = `${state.videos.length} na stronie • Strona ${page} • z pamięci podręcznej`;
        renderPagination();
      } else {
        state.videos = [];
        showSkeletons();
        state.gridCardMap = new Map();
        state.lastAppliedFeedRevision = -1;
        state.lastAppliedFeedUpdatedAt = 0;
        state.lastAppliedFeedVideoCount = -1;
        state.lastAppliedVideosCount = 0;
      }
    } else {
      setFeedRefreshingIndicator(true);
    }''',
)

replace_once(
    'static/video-views.js',
    "        reconcilePage(state.videos, { complete: batchData.complete || batchData.stopped || batchData.catalog_complete });\n        updateFeedCounters(batchData);",
    "        const pageComplete = batchData.complete || batchData.stopped || batchData.catalog_complete;\n"
    "        if (isInitial && !isSamePageRefresh && !renderedFromPageCache) {\n"
    "          // Page transitions and the first app paint use chunked replacement:\n"
    "          // the first 16 cards become visible immediately, the rest follow\n"
    "          // in small deterministic chunks instead of blocking on 280 cards.\n"
    "          renderVideoGrid(state.videos);\n"
    "        } else {\n"
    "          reconcilePage(state.videos, { complete: pageComplete });\n"
    "        }\n"
    "        rememberHomePage(page, specKey, { ...batchData, videos: state.videos, items: state.videos });\n"
    "        updateFeedCounters(batchData);",
)

replace_once(
    'static/video-views.js',
    "      apply(data, true);\n      const catalogRevisionStream = data.catalog_complete === false",
    "      apply(data, true);\n"
    "      // Prepare the most likely next click while the user is looking at the\n"
    "      // current page. JSON and the first posters will usually be warm before\n"
    "      // the paginator is used.\n"
    "      prefetchNextPage();\n"
    "      const catalogRevisionStream = data.catalog_complete === false",
)

# 4) Lock the performance behavior in the normal frontend regression lane.
replace_once(
    'audit/regression_frontend.cjs',
    "  const gridSource = fs.readFileSync('static/video-grid.js', 'utf8');\n  assert.doesNotMatch(gridSource, /requestIdleCallback\\(appendNextChunk/, 'long-grid completion must not depend on idle callbacks');\n  assert.match(gridSource, /setTimeout\\(appendNextChunk, 0\\)/, 'long-grid chunks must have a deterministic scheduler');",
    "  const gridSource = fs.readFileSync('static/video-grid.js', 'utf8');\n"
    "  assert.doesNotMatch(gridSource, /requestIdleCallback\\(appendNextChunk/, 'long-grid completion must not depend on idle callbacks');\n"
    "  assert.match(gridSource, /setTimeout\\(appendNextChunk, 0\\)/, 'long-grid chunks must have a deterministic scheduler');\n"
    "  assert.match(gridSource, /INITIAL_BATCH = 16/, 'first paint must stay bounded to a small synchronous card batch');\n"
    "  const viewsPerfSource = fs.readFileSync('static/video-views.js', 'utf8');\n"
    "  assert.match(viewsPerfSource, /HOME_PAGE_CACHE_LIMIT = 3/, 'home pagination needs a bounded three-page cache');\n"
    "  assert.match(viewsPerfSource, /prefetchHomePage\\(current \\+ 1/, 'home view must prefetch the likely next page');\n"
    "  assert.match(viewsPerfSource, /isInitial && !isSamePageRefresh && !renderedFromPageCache/, 'first page and page transitions must use chunked replacement');",
)

print('Applied home pagination performance hotfix.')
