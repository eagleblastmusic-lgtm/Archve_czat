# Release requirements

This file is the source-native release contract for the repaired generation.

1. **Anonymous startup:** without explicit credentials, the application performs zero remote authentication attempts. No credential literal may be committed.
2. **Process authority:** occupying port 8000 never authorizes terminating a process. Launchers fail safely on conflicts.
3. **Durability:** user-state success requires a successful durable commit; persistence failure propagates and in-memory state rolls back.
4. **Remote effects:** local and remote account outcomes are explicit (`synced`, `local_only`, `remote_failed`, auth/fetch failure). No partial failure is reported as full success.
5. **Async responsiveness:** async account GET handlers never execute full synchronous account synchronization on the event loop.
6. **Catalog state:** only verified natural source completion may publish a complete revision. Hard limits are truncation/failure. Failed revisions expose errors and remain incomplete.
7. **Refresh generation:** force refresh returns a revision token and the UI follows that requested revision rather than silently staying pinned to an older complete revision.
8. **Stable identity:** fallback catalog identities are deterministic across processes.
9. **Storyboard integrity:** FFmpeg non-zero/invalid output is rejected. Precision metadata is based on observed decoded PTS; fallback timing is explicitly approximate.
10. **Evidence:** historical diagnostic JSON is not release evidence. CI commands and exit codes are bound to the commit by GitHub Actions.
11. **Desktop packaging:** every imported runtime dependency, including pywebview, is declared in requirements.

A release-ready decision requires all target regressions and the offline acceptance set to pass on the exact candidate commit.
