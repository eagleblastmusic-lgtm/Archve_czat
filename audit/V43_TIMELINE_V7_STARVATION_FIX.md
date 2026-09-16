# Archivebate V4.3 — timeline V7 starvation fix

## Live symptom that exposed the bug

The browser reported a state equivalent to:

```text
installed=true
pointer_updates=415
requests=0
```

while the hover preview stayed on one image.

That combination is decisive: the V6 fallback was receiving real pointer movement but never started or reused a dynamic coarse storyboard. Its safety rule explicitly prohibited pointer motion from starting QUICK extraction, and background prewarm required an 8-second playback buffer. If that threshold was never reached before the user hovered, the only valid fallback was the poster, so the preview looked frozen even though the pointer handler was active.

## Additional failure mode

V6 also trusted modal-player-controls to have rendered an exact cached frame earlier in the same animation frame. That made correct output depend on event-listener/RAF ordering. V7 removes that assumption and renders an exact cached segment itself whenever one is available.

## V7 architecture

1. `currentVideoDetails.id` is treated as the modal identity source of truth and repairs `currentVideoId`/`video.dataset.videoId` when necessary.
2. Cached exact 1-fps/30-second segments remain authoritative and are rendered directly by the V7 layer.
3. A real cold hover can start one persistent low-resolution QUICK overview once primary playback has at least 3 seconds buffered.
4. Background prewarm remains more conservative: 5 seconds buffered and a delayed start.
5. The first exact request is coordinated with the cold QUICK build. Exact extraction is deferred for at most 3500 ms, so continuous pointer movement no longer cancels the overview every 140 ms.
6. QUICK remains capped at two FFmpeg processes and is tuned to eight 160x90 overview frames in the V4.3 runtime.
7. Waiting, stalled, seeking, and low-buffer playback states still cancel speculative coarse work.
8. No auxiliary full-resolution browser video seek path is reintroduced.

## Expected live evidence

For a cold Archivebate video, after playback has a usable buffer and the user sweeps the timeline:

```text
coordinator_version=7
pointer_updates > 0
source_video_id != ''
hover_coarse_starts >= 1   (cold case)
requests > 0               (cold case)
coarse_builds >= 1         (cold case)
```

After QUICK becomes ready, `frames` should increase while sweeping the full timeline. When exact segment cache is available, `exact_frames` and `exact_cache_hits` should increase as the cursor moves within that segment.

A warm-cache run may legitimately have no new build because it can reuse persistent/cached storyboard data.

## Playback guard

The fix intentionally does not restore the former full-resolution preview seeker. Coarse work is low-resolution and bounded; primary playback can still cancel it when the buffer becomes fragile. The acceptance gate must therefore verify both preview frame changes and click-to-first-frame/buffering behavior before V4.3 is merged.