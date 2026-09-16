# Archivebate V4.5.2 — Player QoS Stabilization

## Goal

Make primary playback the highest-priority workload before increasing storyboard density in V4.6.

## Invariants

1. Cached sprites/posters may render at any time; uncached FFmpeg work may not start during critical playback.
2. At most two storyboard FFmpeg processes globally; while playback is active, at most one decoder runs.
3. `waiting`, `stalled`, `seeking`, or <2 s buffered ahead soft-preempts active storyboard decoders without poisoning future recovered work.
4. QUICK survives transient hover/lease churn, but a real video switch/close or critical playback may cancel it.
5. EXACT is scheduled once per idle dwell and re-checks playback health immediately before start.
6. Storyboard proxy traffic is tagged `owner=storyboard`; it never impersonates player traffic.
7. `/watch` never opens a second full-resolution MP4 for timeline hover.
8. Diagnostics expose source resolution, rendered size, total frames, dropped frames, and corrupted frames.

## Delivery order

- QoS state + backend FFmpeg arbiter.
- Emergency/hard cancellation boundaries.
- Single idle-EXACT coordinator.
- `/watch` auxiliary stream removal.
- Traffic ownership tags.
- Playback-quality telemetry.
- Regression tests and CI gate.

## V4.6 gate

Do not increase QUICK frame density until V4.5.2 passes syntax/regression CI and a live A/B playback run shows no material increase in stalls or dropped-frame ratio with storyboard enabled.
