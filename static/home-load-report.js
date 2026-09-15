(() => {
  'use strict';

  function round(value) {
    const n = Number(value);
    return Number.isFinite(n) ? Math.round(n * 10) / 10 : 0;
  }

  function compactResource(entry) {
    let name = String(entry?.name || '');
    try {
      const url = new URL(name, location.href);
      name = url.origin === location.origin ? `${url.pathname}${url.search}` : `${url.host}${url.pathname}`;
    } catch (_) {}
    return {
      name,
      initiator: entry?.initiatorType || '',
      start_ms: round(entry?.startTime),
      response_start_ms: round(entry?.responseStart),
      response_end_ms: round(entry?.responseEnd),
      duration_ms: round(entry?.duration),
      transfer_size: Number(entry?.transferSize || 0)
    };
  }

  function collect() {
    const nav = performance.getEntriesByType('navigation')[0] || null;
    const resources = performance.getEntriesByType('resource');
    const interesting = resources.filter(entry => {
      const name = String(entry.name || '');
      return /\/api\/feed(?:\/stream)?\?/.test(name) ||
        /\/api\/thumb\?/.test(name) ||
        /cdnjs\.cloudflare\.com/.test(name);
    }).map(compactResource);

    const feed = interesting.filter(item => item.name.includes('/api/feed'));
    const thumbs = interesting.filter(item => item.name.includes('/api/thumb'));
    const fontAwesome = Array.from(document.querySelectorAll('link[rel="stylesheet"]'))
      .find(node => String(node.href || '').includes('font-awesome'));
    const cards = document.querySelectorAll('#videoGrid .video-card').length;
    const perfTimings = Array.isArray(globalThis.ArchivebatePerf?.timings)
      ? globalThis.ArchivebatePerf.timings.filter(item => String(item?.name || '').includes('feed')).map(item => ({ name: item.name, ms: round(item.ms) }))
      : [];

    return {
      schema: 'archivebate-home-load-report/1',
      generated_at: new Date().toISOString(),
      elapsed_ms: round(performance.now()),
      cards,
      grouped: Boolean(globalThis.ArchivebateAppContext?.state?.groupByAuthor || globalThis.state?.groupByAuthor),
      source_filter: globalThis.ArchivebateAppContext?.state?.sourceFilter || globalThis.state?.sourceFilter || null,
      author_filter: globalThis.ArchivebateAppContext?.state?.authorFilter || globalThis.state?.authorFilter || null,
      catalog_revision: globalThis.ArchivebateAppContext?.state?.catalogRevision || globalThis.state?.catalogRevision || null,
      navigation: nav ? {
        response_end_ms: round(nav.responseEnd),
        dom_content_loaded_ms: round(nav.domContentLoadedEventEnd),
        load_event_ms: round(nav.loadEventEnd)
      } : null,
      app_timings: perfTimings,
      feed_requests: feed,
      feed_total_ms: round(feed.reduce((sum, item) => sum + item.duration_ms, 0)),
      thumbnail_requests: thumbs.length,
      slowest_thumbnails: [...thumbs].sort((a, b) => b.duration_ms - a.duration_ms).slice(0, 10),
      icons: {
        stylesheet_found: Boolean(fontAwesome),
        media: fontAwesome?.media || '',
        sheet_loaded: Boolean(fontAwesome?.sheet),
        local_fallback_active: document.documentElement.classList.contains('archivebate-local-icons')
      }
    };
  }

  function print() {
    const report = collect();
    console.log('Archivebate home load report');
    console.log(JSON.stringify(report, null, 2));
    return report;
  }

  globalThis.ArchivebateHomeLoadDiagnostics = { collect, print };
})();
