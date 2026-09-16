# Archivebate V4.5.2 — Player QoS live Windows gate

This gate is intentionally run against a real Archivebate stream **after CI is green**. It does not save playback position, add watched markers or mutate the catalog database.

The V4.5.2 timeline architecture is progressive: **exact 1-fps segment > playback-safe coarse QUICK sprite > poster/status**. Primary playback always outranks speculative storyboard work. Pointer movement must never seek or load a second full-resolution MP4.

## Setup

1. Run branch `v4.3-performance-playback` with the normal Desktop launcher (`desktop_app.py`) or the guarded browser launcher. Do **not** run `uvicorn main:app`, because that bypasses the V4.5.2 runtime/QoS wiring.
2. Open an Archivebate video (not Camwhores) that has not just been tested repeatedly from cache.
3. Observe click-to-first-frame before touching the timeline.
4. Hover the timeline immediately, move across several positions and leave it. Cold hover may show poster/status, but it must **never become a black preview surface** and must not destabilize playback.
5. Let playback continue until it has a healthy buffer. Background EXACT prewarm is intentionally allowed only with at least **8 s** buffered ahead.
6. Move across several distant timeline positions. Pointer motion itself should render only already-prepared exact/coarse/poster data. Stop for at least **300 ms** at a distant position: the modal coordinator uses a **260 ms** exact-idle dwell, then re-checks playback health before delegating to the exact client.
7. During healthy playback, an intentional uncached EXACT may start only with at least **3 s** buffered ahead. The exact client performs a second QoS check immediately before acquiring demand / starting backend work.
8. If playback enters `waiting`, `stalled`, `seeking`, or drops below the critical buffer threshold, storyboard work should yield/cancel rather than competing with playback.
9. Leave the timeline for at least 3 seconds, then collect the report. Active target/FFmpeg work should drain back to zero.

## Collect the report

Open DevTools Console and run:

```js
await import('/static/v43-live-report.js?v=' + Date.now());
JSON.stringify(await ArchivebateV452Diagnostics.print(), null, 2)
```

The expected report schema is:

```text
archivebate-v452-live-report/1
```

For compatibility the same collector is also exported as `ArchivebateV43Diagnostics`, but use `ArchivebateV452Diagnostics` for this gate.

## Required observations

### Timeline coordinator

- `timeline.fallback.coordinator_version` is `452`.
- `timeline.fallback.player_qos_coordinator` is `true`.
- `timeline.fallback.media_seek_enabled` is `false`.
- `timeline.fallback.black_fallback_enabled` is `false`, and the real UI confirms that cold hover shows poster/status rather than black.
- `timeline.fallback.exact_idle_ms` is `260`.
- `timeline.fallback.exact_min_buffer_seconds` is `3`.
- Pointer movement may increase `pointer_updates`, but it must not translate into one backend EXACT start per crossed segment.
- `exact_idle_resets` may rise during fast movement; `exact_idle_starts` should represent settled intent, not raw pointer churn.

### Exact storyboard client

- `timeline.exact_client.player_qos_guard` is `true`.
- `timeline.exact_client.hover_intent_ms` is `140`.
- `timeline.exact_client.exact_buffer_seconds` is `3`.
- `timeline.exact_client.background_buffer_seconds` is `8`.
- During weak playback, `qos_blocked_starts`, `qos_wait_loops` or `qos_prewarm_skips` may increase. This is expected: playback outranks storyboard generation.
- Revisiting a prepared exact segment increases cache hits and must not create a second backend build for the same prepared segment.
- After pointerleave / modal lifecycle cancellation, `segment_inflight`, `warm_inflight`, `active_target_requests` and `target_leases` must return to `0` after cancellation drains.

### QUICK / coarse storyboard

- Coarse QUICK remains a low-cost fallback and must not be killed merely because the pointer crosses another exact segment.
- Ordinary cursor churn must not repeatedly restart QUICK.
- Real lifecycle/QoS events may abort QUICK when playback protection requires it.
- `timeline.coarse_server.exact_preempts_quick` should be `false` in V4.5.2; exact target churn must no longer kill shared QUICK just because a different exact target is selected.
- `timeline.coarse_server.active_quick_processes` must return to `0` after hover/lifecycle work drains.

### Player QoS

- `qos.server` is available and reports the V4.5.2 runtime arbitration state.
- Global storyboard FFmpeg concurrency must never exceed **2**.
- While primary playback is active, at most **1** storyboard decoder may run.
- Soft protection should activate for critical playback states instead of letting speculative storyboard decoding continue unchecked.
- Changing/closing the video must hard-cancel stale storyboard work for the previous video.

### Playback quality

Check `player` in the report:

- `video_width` / `video_height` identify decoded source resolution.
- `display_width` / `display_height` identify rendered size.
- `total_video_frames`, `dropped_video_frames`, `corrupted_video_frames` and `dropped_frame_ratio` are captured when the browser exposes them.
- `stall_count` and `stall_duration_ms` must not materially worsen when timeline hover/storyboard generation is exercised.
- Playback startup must not materially regress. Compare several `playback.click_to_first_frame` samples with the previous V4.3 live samples (roughly 1.9–2.5 s on the previously tested source); do not decide from one noisy network sample.

## Suggested live sequence

Run at least these three observations on the same video:

1. **Playback only** — start the video and do not touch the timeline for ~15 s; capture a report.
2. **Heavy timeline interaction** — restart/open the video, move rapidly across distant timeline positions, pause briefly (>300 ms) on several positions, revisit prepared positions, then leave the timeline; capture a report.
3. **Lifecycle/QoS** — while storyboard work is active, seek or switch/close the video; reopen and verify playback recovers normally; capture a report after work drains.

Compare the three reports primarily for stalls, dropped frames, click-to-first-frame, buffer behaviour, queue depth and whether stale storyboard work returns to zero.

## Failure conditions

Keep PR #4 in Draft if any of these occur:

- black cold-hover preview,
- auxiliary full-resolution preview stream or timeline media seeks return,
- pointer motion produces an EXACT/status-request storm,
- more than 2 storyboard FFmpeg processes run concurrently,
- more than 1 storyboard decoder competes while primary playback is active,
- uncached EXACT starts while active playback has less than 3 s safe buffer,
- background EXACT prewarm starts below the 8 s threshold,
- ordinary cursor churn repeatedly kills/restarts QUICK,
- playback startup/stability materially regresses,
- dropped-frame ratio or stall duration materially worsens under timeline interaction,
- stale work remains active after pointerleave, seek, video switch or close,
- queue depth grows without draining,
- `active_target_requests`, `segment_inflight`, `warm_inflight`, target leases or backend active processes remain non-zero after the interaction has ended.

The design and implementation rationale are documented in `audit/V45_2_PLAYER_QOS_PLAN.md` and the earlier timeline audit material.