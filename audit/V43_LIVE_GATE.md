# Archivebate V4.3 — live Windows gate

This gate is intentionally run against a real Archivebate stream after CI is green. It does not save playback position, add watched markers or mutate the catalog database.

The current V4.3 timeline architecture is progressive: **exact 1-fps segment > playback-safe coarse sprite > poster**. Pointer movement never seeks a second full-resolution MP4.

## Setup

1. Run branch `v4.3-performance-playback` with the normal Desktop launcher (`desktop_app.py`) or the guarded browser launcher. Do not run `uvicorn main:app`, because that bypasses V4.3 runtime wiring.
2. Open an Archivebate video (not Camwhores) that has not just been tested repeatedly from cache.
3. Observe click-to-first-frame before touching the timeline.
4. Hover the timeline immediately, then leave it. The cold tooltip must retain the poster/status; it must **never become a black preview surface**.
5. Let playback continue for about 8–12 seconds. When playback has a healthy buffer, the tiny coarse sprite may be prepared in the background.
6. Move across several distant timeline positions. A ready coarse sprite should change locally without media seeks. Stop for >140 ms at a distant position so the exact 30-second segment can replace coarse/poster when ready.
7. Leave the timeline for at least 3 seconds, then collect the report.

## Collect the report

Open DevTools Console and run:

```js
await import('/static/v43-live-report.js?v=' + Date.now());
JSON.stringify(await ArchivebateV43Diagnostics.print(), null, 2)
```

`archivebate-v43-live-report/3` contains exact client/server metrics, V4.3 fallback metrics and coarse-server metrics in one object.

## Required observations

- `timeline.fallback.media_seek_enabled` is `false`.
- `timeline.fallback.black_fallback_enabled` is `false`, and the real UI confirms that cold hover shows a poster/status rather than black.
- `timeline.fallback.status_requests` stays low and does not scale with `pointer_updates`; pointer motion must not create a polling storm.
- `timeline.coarse_server.parallelism` is `2`, `frame_count` is `4`, `active_quick_processes <= 2`, and `exact_preempts_quick` is `true`.
- If playback is fragile, `playback_protect_skips`, `low_buffer_cancels` or `coarse_build_aborts` may increase. That is expected: playback outranks speculative preview work.
- Prepared/warm exact segments change frames immediately while scrubbing; `timeline.exact_client.exact_ready_p95_ms` should remain near the previous warm result (~49 ms) when the requested segment is already prepared.
- Revisiting a prepared exact segment increases cache hits and does not create another backend segment build.
- After rapid distant jumps, stale exact work is preempted/cancelled instead of accumulating; server preempted/cancelled counters may increase.
- After leaving the timeline and waiting, exact `segment_inflight`, `active_target_requests`, server `active_processes`, `building_jobs`, and coarse `active_quick_processes` all return to `0` after cancellation drains.
- `timeline.exact_server.queue_size` does not keep growing after cursor movement stops.
- Playback startup must not materially regress. Compare several `playback.click_to_first_frame` samples with the pre-coarse V4.3 live samples (roughly 1.9–2.5 s on the previously tested source); do not decide from one noisy network sample.

## Failure conditions

Keep PR #4 in Draft if any of these occur:

- black cold-hover preview,
- auxiliary full-resolution preview stream/media seeks return,
- pointer motion produces a status-request storm,
- more than 2 QUICK FFmpeg processes run concurrently,
- QUICK continues while exact hover is active instead of yielding,
- playback startup/stability visibly regresses,
- stale work remains active after hover/close,
- queue depth grows without draining.

The detailed root-cause analysis and corrective design are documented in `audit/V43_TIMELINE_DEEP_AUDIT.md`.
