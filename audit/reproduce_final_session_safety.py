"""Reproduction & Deterministic Concurrency Test Suite for:
Test A: release vs acquire race (deterministic synchronization).
Test B: stale build / session fencing.
Test C: reopen (open -> close -> open) with delayed release/renew.
Test D: cancel without session_id cannot kill active session.
Test E: source isolation (archivebate vs camwhores with same ID).
Test F: lease limit & automatic cleanup (protect active leases over 1000 keys).

Proves RED on baseline, and GREEN on hardened implementation.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import player_qos_runtime as qos
import timeline_storyboard as ts
from timeline_storyboard import resolve_video_key


class MockProcess:
    def __init__(self, key: str = ""):
        self.key = key
        self.terminated = False
        self.killed = False

    def poll(self):
        return 0 if (self.terminated or self.killed) else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


def test_a_release_vs_acquire_race():
    """Test A: release(A) race against acquire(B).
    
    1. Session A releases its lease (remaining == 0).
    2. Synchronize before hard_cancel is executed.
    3. Session B acquires a new lease and registers an active process.
    4. Release(A)'s hard_cancel executes.
    5. Verify Session B's lease, QoS state, and process are NOT cancelled or terminated.
    """
    print("\n--- TEST A: release vs acquire race ---")
    vid = f"test_vid_race_{int(time.monotonic() * 1000)}"
    key = resolve_video_key(vid, source="archivebate")

    # Session A acquires
    ts.lease_manager.acquire(key, consumer_id="consumer_A", session_id="sess_A", duration=30.0)
    assert ts.lease_manager.has_live_lease(key) is True
    assert not qos.is_hard_cancelled(key.provider_id, source=key.source)

    # Process for Session A
    proc_a = MockProcess("proc_a")
    qos.register_v2_process("proc_a", proc_a, key.provider_id, source=key.source)

    # Synchronization points
    in_cancel_event = threading.Event()
    resume_cancel_event = threading.Event()

    original_hard_cancel = qos.hard_cancel

    def hooked_hard_cancel(video_id=None, source=None, reason="client", clear_lease=True, generation=None):
        # Signal that Thread 1 has determined cancellation and reached hard_cancel
        in_cancel_event.set()
        # Wait for Session B to acquire in the main thread
        resume_cancel_event.wait(timeout=5.0)
        try:
            return original_hard_cancel(video_id=video_id, source=source, reason=reason, clear_lease=clear_lease, generation=generation)
        except TypeError:
            return original_hard_cancel(video_id=video_id, source=source, reason=reason, clear_lease=clear_lease)

    qos.hard_cancel = hooked_hard_cancel

    proc_b = MockProcess("proc_b")

    def release_thread_fn():
        ts.lease_manager.release(key, consumer_id="consumer_A", session_id="sess_A", reason="tab_a_close")

    t1 = threading.Thread(target=release_thread_fn, name="ReleaseThread")
    try:
        t1.start()
        # Wait until Thread 1 has decided to cancel and is paused right before hard_cancel
        assert in_cancel_event.wait(timeout=3.0), "Thread 1 did not reach hooked hard_cancel"

        # Now, concurrently, Session B acquires a new lease for the same video!
        res_b = ts.lease_manager.acquire(key, consumer_id="consumer_B", session_id="sess_B", duration=30.0)
        assert res_b.ok is True, "Session B acquire must succeed"
        assert ts.lease_manager.has_live_lease(key) is True

        # Session B registers its active decoder process
        qos.register_v2_process("proc_b", proc_b, key.provider_id, source=key.source)
        assert not qos.is_hard_cancelled(key.provider_id, source=key.source)

        # Unpause Thread 1 to execute Session A's delayed hard_cancel
        resume_cancel_event.set()
        t1.join(timeout=3.0)

        # VERIFICATION:
        # Session A's delayed cancel must NOT destroy Session B!
        assert not proc_b.terminated, "RED: Delayed cancel of Session A terminated Session B's process!"
        assert not qos.is_hard_cancelled(key.provider_id, source=key.source), "RED: Delayed cancel of Session A marked Session B as hard cancelled!"
        assert ts.lease_manager.has_live_lease(key) is True, "RED: Session B lost its live lease!"
        print("PASS: Test A: Session B was protected from Session A's delayed cancellation")
    finally:
        qos.hard_cancel = original_hard_cancel
        qos.unregister_v2_process("proc_a")
        qos.unregister_v2_process("proc_b")
        ts.lease_manager.release(key, consumer_id="consumer_B", session_id="sess_B", reason="cleanup")


def test_b_stale_build():
    """Test B: stale build fencing.
    
    Delivery: build(A) -> build(B) -> delayed build(A).
    Confirm that stale build(A) does not overwrite or invalidate active Session B.
    """
    print("\n--- TEST B: stale build fencing ---")
    vid = f"test_vid_build_{int(time.monotonic() * 1000)}"
    key = resolve_video_key(vid, source="archivebate")
    cid = "tab_browser_1"

    # 1. Build A
    res_a = ts.lease_manager.acquire(key, consumer_id=cid, session_id="sess_A", duration=30.0)
    assert res_a.ok is True

    # 2. Build B (reopened in same tab)
    res_b = ts.lease_manager.acquire(key, consumer_id=cid, session_id="sess_B", duration=30.0)
    assert res_b.ok is True

    # 3. Delayed build(A) arrives
    res_a_delayed = ts.lease_manager.acquire(key, consumer_id=cid, session_id="sess_A", duration=30.0)
    print(f"Delayed acquire A result: ok={res_a_delayed.ok}, session_id={res_a_delayed.session_id}")

    # Check active record in lease manager
    with ts.lease_manager._lock:
        rec = ts.lease_manager._leases.get(key.as_string(), {}).get(cid, {})
        current_sid = rec.get("session_id")

    assert current_sid == "sess_B", f"RED: Delayed acquire A overwrote Session B with '{current_sid}'!"
    assert res_a_delayed.ok is False, "RED: Delayed acquire of stale session must return ok=False!"

    # Session B can still renew
    renew_ok, count = ts.lease_manager.renew(key, consumer_id=cid, session_id="sess_B", duration=30.0)
    assert renew_ok is True, "Session B must be renewable"
    print("PASS: Test B: Stale build(A) did not overwrite active Session B")
    ts.lease_manager.release(key, consumer_id=cid, session_id="sess_B", reason="cleanup")


def test_c_reopen_and_delayed_stale_release_renew():
    """Test C: reopen (open X -> close X -> open X) with delayed release/renew from old session."""
    print("\n--- TEST C: reopen and delayed release/renew ---")
    vid = f"test_vid_reopen_{int(time.monotonic() * 1000)}"
    key = resolve_video_key(vid, source="archivebate")
    cid = "tab_1"

    # 1. Open X (session 1)
    ts.lease_manager.acquire(key, consumer_id=cid, session_id="sess_1", duration=30.0)

    # 2. Close X (session 1)
    ts.lease_manager.release(key, consumer_id=cid, session_id="sess_1", reason="close_1")
    assert not ts.lease_manager.has_live_lease(key)

    # 3. Open X again (session 2)
    res_2 = ts.lease_manager.acquire(key, consumer_id=cid, session_id="sess_2", duration=30.0)
    assert res_2.ok is True
    assert ts.lease_manager.has_live_lease(key) is True

    # 4. Out-of-order delayed release from session 1 arrives
    was_present, remaining = ts.lease_manager.release(key, consumer_id=cid, session_id="sess_1", reason="delayed_close_1")
    assert was_present is False, "Stale release from session 1 must report was_present=False"
    assert remaining == 1, f"Expected 1 remaining active consumer, got {remaining}"
    assert ts.lease_manager.has_live_lease(key) is True, "Session 2 must remain live after stale release"

    # 5. Out-of-order delayed renew from session 1 arrives
    renew_ok, _ = ts.lease_manager.renew(key, consumer_id=cid, session_id="sess_1", duration=30.0)
    assert renew_ok is False, "Stale renew from session 1 must be rejected"

    # 6. Session 2 renews successfully
    renew_2_ok, _ = ts.lease_manager.renew(key, consumer_id=cid, session_id="sess_2", duration=30.0)
    assert renew_2_ok is True, "Session 2 renew must succeed"

    print("PASS: Test C: Reopen and delayed release/renew handled safely")
    ts.lease_manager.release(key, consumer_id=cid, session_id="sess_2", reason="cleanup")


def test_d_cancel_without_session_id():
    """Test D: cancel without session_id must NOT delete newer session."""
    print("\n--- TEST D: cancel without session_id ---")
    vid = f"test_vid_nosid_{int(time.monotonic() * 1000)}"
    key = resolve_video_key(vid, source="archivebate")
    cid = "tab_client"

    # Consumer acquires with specific session_id
    res = ts.lease_manager.acquire(key, consumer_id=cid, session_id="sess_active_123", duration=30.0)
    assert res.ok is True
    assert ts.lease_manager.has_live_lease(key) is True

    # An incomplete or malicious cancel request arrives for cid without session_id
    was_present, remaining = ts.lease_manager.release(key, consumer_id=cid, session_id=None, reason="unscoped_cancel")
    print(f"Unscoped cancel result: was_present={was_present}, remaining={remaining}")

    assert was_present is False, "RED: Release without session_id removed active session with session_id!"
    assert ts.lease_manager.has_live_lease(key) is True, "RED: Active lease was destroyed by unscoped cancel!"

    # Clean up with matching session_id
    was_present_clean, _ = ts.lease_manager.release(key, consumer_id=cid, session_id="sess_active_123", reason="cleanup")
    assert was_present_clean is True
    print("PASS: Test D: Cancel without session_id cannot kill active session")


def test_e_source_isolation():
    """Test E: source isolation with identical provider_id across archivebate and camwhores."""
    print("\n--- TEST E: source isolation ---")
    same_id = f"iso_{int(time.monotonic() * 1000)}"
    key_ab = resolve_video_key(same_id, source="archivebate")
    key_cw = resolve_video_key(same_id, source="camwhores")

    # Acquire lease on both
    ts.lease_manager.acquire(key_ab, consumer_id="c_ab", session_id="s_ab", duration=30.0)
    ts.lease_manager.acquire(key_cw, consumer_id="c_cw", session_id="s_cw", duration=30.0)

    # Register mock processes on both
    proc_ab = MockProcess("proc_ab")
    proc_cw = MockProcess("proc_cw")
    qos.register_v2_process("p_ab", proc_ab, key_ab.provider_id, source=key_ab.source)
    qos.register_v2_process("p_cw", proc_cw, key_cw.provider_id, source=key_cw.source)

    # Cancel archivebate
    ts.lease_manager.release(key_ab, consumer_id="c_ab", session_id="s_ab", reason="close_ab")

    # Verify archivebate is cancelled and proc_ab terminated
    assert not ts.lease_manager.has_live_lease(key_ab)
    assert proc_ab.terminated is True

    # Verify camwhores is NOT affected
    assert ts.lease_manager.has_live_lease(key_cw) is True, "RED: Camwhores lease was affected by archivebate cancel!"
    assert proc_cw.terminated is False, "RED: Camwhores process was terminated by archivebate cancel!"
    assert not qos.is_hard_cancelled(key_cw.provider_id, source=key_cw.source), "RED: Camwhores marked cancelled!"

    print("PASS: Test E: Source isolation fully verified across leases and QoS processes")
    ts.lease_manager.release(key_cw, consumer_id="c_cw", session_id="s_cw", reason="cleanup")


def test_f_lease_limit_and_automatic_cleanup():
    """Test F: lease limit (max_tracked_keys = 1000) protects active leases and cleans up expired.
    
    Tests over 1000 active and expired keys:
    1. Register 1000 active keys.
    2. Attempting to add a 1001st key when capacity is full of active keys triggers explicit overload status (ok=False)
       and protects all active keys from eviction.
    3. When leases expire, automatic pruning purges expired keys and allows new acquisitions.
    4. Active leases are never evicted while heartbeats continue.
    """
    print("\n--- TEST F: lease limit and automatic cleanup (>1000 keys) ---")
    mgr = ts.ConsumerLeaseManager(default_duration=120.0, max_tracked_keys=1000)

    # 1. Fill manager with 1000 active keys
    active_keys = [resolve_video_key(f"active_{i}", source="archivebate") for i in range(1000)]
    for k in active_keys:
        res = mgr.acquire(k, consumer_id=f"c_{k.provider_id}", session_id="s1", duration=100.0)
        assert res.ok is True

    assert len(mgr._leases) == 1000, f"Expected 1000 leases, got {len(mgr._leases)}"

    # 2. Capacity overload check: 1001st active key must NOT evict any active lease
    overflow_key = resolve_video_key("overflow_1001", source="archivebate")
    res_overflow = mgr.acquire(overflow_key, consumer_id="c_overflow", session_id="s1", duration=100.0)
    assert res_overflow.ok is False, "Overload capacity check must reject acquire when all slots are active"
    assert len(mgr._leases) == 1000

    # Verify all 1000 original active leases are still intact and protected
    assert mgr.has_live_lease(active_keys[0]) is True
    assert mgr.has_live_lease(active_keys[999]) is True

    # 3. Simulate 500 keys naturally expiring
    now = time.monotonic()
    with mgr._lock:
        for k in active_keys[:500]:
            k_str = k.as_string()
            for rec in mgr._leases[k_str].values():
                rec["expires"] = now - 1.0  # expired

    # 4. Now acquire a new key: pruning must purge the 500 expired keys and accept the new key!
    res_after_expiry = mgr.acquire(overflow_key, consumer_id="c_overflow", session_id="s1", duration=100.0)
    assert res_after_expiry.ok is True, "Acquire must succeed after expired keys are pruned"

    # All remaining 500 active keys must STILL be live!
    for k in active_keys[500:520]:
        assert mgr.has_live_lease(k) is True, f"Active key {k.as_string()} must remain live!"

    # Expired keys must not be live
    assert not mgr.has_live_lease(active_keys[0])
    assert not mgr.has_live_lease(active_keys[499])

    # Memory bounded: retired sessions and epochs for expired keys were pruned
    with mgr._lock:
        assert active_keys[0].as_string() not in mgr._leases
        assert active_keys[0].as_string() not in mgr._epochs
        assert active_keys[0].as_string() not in mgr._retired_sessions

    print("PASS: Test F: Active leases protected from eviction, expired leases cleaned up automatically (>1000 keys)")


if __name__ == "__main__":
    tests = [
        test_a_release_vs_acquire_race,
        test_b_stale_build,
        test_c_reopen_and_delayed_stale_release_renew,
        test_d_cancel_without_session_id,
        test_e_source_isolation,
        test_f_lease_limit_and_automatic_cleanup,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"FAILED: {t.__name__}: {e}")
            failed += 1
    print(f"\n==========================================")
    print(f"Result: {passed} passed, {failed} failed")
    print(f"==========================================")
    if failed > 0:
        sys.exit(1)
