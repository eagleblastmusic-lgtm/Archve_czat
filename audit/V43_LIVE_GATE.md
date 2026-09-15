# Archivebate V4.3 — live Windows gate

This gate is intentionally run against a real Archivebate stream after CI is green. It does not write playback position/history and does not mutate the catalog database.

## Setup

1. Run the branch `v4.3-performance-playback` with the normal desktop launcher.
2. Open an Archivebate video (not Camwhores) and let playback begin.
3. After the player has buffered at least a couple of seconds, move the timeline cursor across the current 30-second segment, then jump to a distant segment, then immediately jump again to another distant segment.
4. Return to a segment that was already prepared and scrub across several consecutive seconds.
5. Leave the timeline for at least two seconds.

## Collect the report

Open DevTools Console and run:

```js
await import('/static/v43-live-report.js');
await ArchivebateV43Diagnostics.print();
```

Copy the returned JSON object for the release evidence.

## Required observations

- `timeline.client.full_upgrade_enabled` is `false`.
- `timeline.client.cold_quick_generation_enabled` is `false`.
- Prepared/warm segments change frames immediately while scrubbing; frame selection is already covered by the automated <= 1 s time-error contract.
- Revisiting a prepared segment increases client cache hits and does not create another backend segment build.
- After rapid distant jumps, stale work is preempted/cancelled instead of accumulating; `timeline.server.preempted_processes` and/or `cancelled_processes` may increase.
- After leaving the timeline and waiting, `timeline.client.segment_inflight` and `active_target_requests` return to `0`, and server `active_processes` returns to `0` once the cancellation has drained.
- `timeline.server.queue_size` does not continue growing after cursor movement stops.
- Playback still starts normally. Compare `playback.click_to_first_frame` with the pre-V4.3/local baseline if one is available; V4.3 must not introduce a material regression.

## Useful metrics

`timeline.client.exact_ready_p50_ms/p95_ms` measure cold exact-segment readiness including sprite image load. `timeline.server.segment_ffmpeg_p50_ms/p95_ms` isolate backend FFmpeg cost. `timeline.server.queue_wait_p50_ms/p95_ms` expose scheduler contention. `playback.click_to_first_frame` measures the full user-visible playback startup path.

Do not merge only because one warm-cache run looks fast. Keep the PR draft if cold exact-segment generation repeatedly stalls, stale work remains active after hover cancellation, queue depth grows without draining, or click-to-first-frame visibly regresses.
