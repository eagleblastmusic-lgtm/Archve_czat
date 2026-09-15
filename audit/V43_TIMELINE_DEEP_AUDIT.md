# V4.3 Timeline Preview — deep audit and corrective architecture

Status: **implementation candidate; live re-validation required before merge**.

This audit is scoped to the modal video timeline preview and its interaction with primary playback. Home/feed behavior is deliberately out of scope and remains protected by `regression_v43_home_scope.py`.

## 1. Observed live symptoms

The live tests exposed three distinct phases:

1. The original exact-segment implementation could create a request/process storm during fast cursor motion. A 140 ms intent gate fixed most of that: requests fell from roughly 259 to 24 and exact warm cache display reached about 49 ms p95.
2. A full-resolution auxiliary `<video>` fallback fixed the static-poster correctness bug, but seeking a remote MP4 for each new pointer target was intrinsically slow. One test showed hundreds of pointer requests for only a handful of decoded frames.
3. The first coarse-sprite replacement removed per-pointer media seeking, but introduced a new regression: the tooltip was black while the sprite was building and the film itself appeared slower to start/play.

The third regression is the subject of this repair.

## 2. Root causes found in code

### RC1 — cold hover explicitly hid the only useful image

`static/v43-timeline-fallback.js` called `hideMediaFallback()` before a QUICK sprite was ready. That hid both `modalTimelinePreviewVideo` and `modalTimelinePreviewImg`. The baseline `modal-player-controls.js` had just shown the video's poster, so the V4.3 layer erased the correct fallback and left a black preview surface.

**Correction:** cold hover now preserves/reasserts the poster. The poster is hidden only *after* `ArchivebateYouTubeStoryboard.applyFrame()` returns a valid sprite frame.

### RC2 — status polling moved the request storm instead of removing it

The first coarse implementation polled `/api/storyboard` every 120–240 ms for up to 12 seconds. A single build could therefore produce tens of localhost/ASGI requests even though the underlying FFmpeg work had not changed.

**Correction:** V4.3 now exposes `/api/runtime/v43/storyboard/quick`, a server-side condition-based long poll (default 1200 ms, bounded to 2000 ms). The browser performs one cache probe, one protected start request if needed, then about one status request per long-poll interval rather than 4–8 requests per second.

### RC3 — speculative QUICK generation competed directly with primary playback

The first accelerator used 8 target frames and `QUICK_PARALLELISM = 4`. Every FFmpeg seek read through `/api/video/stream`, which in turn opens a remote MP4/range request. Therefore a playing film could be competing with four simultaneous speculative upstream readers plus exact storyboard work.

**Correction:** coarse QUICK is reduced to 4 frames at 160×90, with at most 2 parallel fast keyframe seeks. Accurate retries are serial and stop after three unique frames succeed.

### RC4 — QUICK was protected from exact hover preemption

The previous patch replaced `_preempt_active_for_target()` with a variant that intentionally skipped processes whose kind was `quick`. That made speculative work survive while an interactive exact 30-second segment started. It violated the intended priority order.

**Correction:** that override is removed. The baseline exact-target preemption function is authoritative again. In addition, QUICK's cancellation predicate checks `_current_desired(video_id)`, so exact hover cancels speculative work immediately.

### RC5 — prewarm started too early

The previous V4.3 layer attempted coarse generation about 450 ms after `playing` with only ~3 seconds of buffer. That overlaps the most fragile startup period measured by `click_to_first_frame` and can increase upstream contention.

**Correction:** background prewarm waits 2500 ms and, while playing, requires `readyState >= 3` plus at least 8 seconds buffered ahead. If the film is already paused after decoding, the threshold can be relaxed because no active playback can stall.

### RC6 — coarse work did not yield again when playback became unhealthy

Once started, speculative extraction could continue through a `waiting`, `stalled`, seek, or buffer collapse.

**Correction:** `waiting`, `stalled`, and primary-player `seeking` abort active coarse work. A timeupdate guard also aborts it when buffered-ahead falls below 3 seconds.

### RC7 — two renderers were fighting instead of sharing one state

The baseline player already implements the correct visual priority:

`exact segment -> coarse board -> poster`.

The V4.3 fallback previously maintained a parallel renderer and could undo baseline output.

**Correction:** when a coarse board becomes ready it is assigned to `state.timelineSpriteBoard`, so the baseline renderer can use the same board. The V4.3 layer only supplies protected generation, cache ownership and a no-black fallback guard.

## 3. Corrective architecture

The new priority chain is:

1. **Primary playback** — always highest priority.
2. **Interactive exact segment** — 30-second, 1 fps, 160×90 sprite; started only after the existing 140 ms hover intent gate.
3. **Speculative coarse sprite** — four 160×90 frames for the whole film; persistent disk cache; generated only when playback is healthy and no hover is active.
4. **Poster** — immediate zero-build fallback. It remains visible until a real sprite frame has been successfully applied.

Pointer motion itself performs no MP4 seek and starts no coarse generator. Once a sprite is cached, pointer motion is a local `translate3d()` frame selection.

## 4. Resource budgets after repair

- Auxiliary media-seek fallback: **disabled**.
- Coarse frames: **4 × 160×90**.
- Coarse FFmpeg parallelism: **2 maximum**.
- Accurate coarse retries: **serial**, stop at 3 successful unique frames.
- Exact segment: unchanged at **1 fps / 30 s / 160×90**.
- Coarse background start: **>= 2.5 s after playing** and **>= 8 s buffered ahead**.
- Coarse cancellation: `waiting`, `stalled`, player seek, buffer `< 3 s`, video switch, or exact-hover priority.
- Browser status polling: replaced by **~1.2 s server-side long poll**.

## 5. Telemetry added

`ArchivebateV43TimelineFallback.stats()` now reports, among others:

- `poster_fallbacks`
- `coarse_builds`, `coarse_ready`, `coarse_build_aborts`
- `status_requests`, `start_requests`, `demand_requests`
- `playback_protect_skips`, `low_buffer_cancels`
- `coarse_ready_p50_ms`, `coarse_ready_p95_ms`
- `media_seek_enabled: false`
- `black_fallback_enabled: false`

`/api/runtime/v43/storyboard/stats` reports backend coarse cost/concurrency, including active QUICK processes and whether exact preempts QUICK.

`static/v43-live-report.js` schema 3 combines exact client/server, fallback and coarse-server telemetry in one report.

## 6. Acceptance gates before merge

V4.3 must not be merged based only on synthetic CI. A real Desktop test must satisfy all of the following:

- Cold timeline hover shows a poster/status, never a black preview surface.
- Moving the pointer does not open an auxiliary media stream and `media_seek_enabled` stays false.
- `status_requests` stays low; it must not scale with pointer events.
- Backend `active_quick_processes <= 2` at all times.
- Exact hover cancels/preempts QUICK rather than running beside it.
- Primary playback startup is not materially worse than the pre-coarse V4.3 samples (~1.9–2.5 s observed on the tested source; compare multiple samples, not one run).
- A cached coarse board changes frames locally without network seeks.
- Warm exact frame display remains fast (previous live p95 ~48.6 ms).
- After hover/close, exact and coarse active processes/leases drain to zero (allowing lease TTL only if a DELETE was genuinely lost; that should be treated as a separate cleanup defect).

## 7. Remaining limitation

Archivebate does not currently expose an already-built YouTube-style storyboard in the metadata consumed by this application. Therefore the *first* preview for an uncached film cannot be both fully dynamic and zero-cost. The repaired design chooses playback safety and visual continuity first: poster immediately, then cached coarse/exact sprites when safely available. Subsequent visits benefit from persistent storyboard cache.
