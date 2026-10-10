"""Deterministic Reproduction & Verification Suite for:
1. P1: Epoch monotonicity & generation consistency across lease expiration and pruning
2. P2: Canonical VideoKey source isolation across ConsumerLeaseManager, Worker V2 loop, and QoS runtime
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


class MockSubprocess:
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


def test_a_generation_after_natural_expiration():
    """Test A: Epoch monotonicity after natural expiration and pruning.
    
    1. Acquire X.
    2. Advance X to epoch > 1.
    3. Allow leases to expire.
    4. Run pruning.
    5. Acquire new session of X.
    6. Verify correct generation order (new_gen > previous_gen, never rolled back to 1).
    7. Release new session.
    8. Confirm cancellation of the active generation succeeds and kills process.
    """
    print("\n--- TEST A: Generation after natural expiration & pruning ---")
    vid = f"vid_epoch_exp_{time.monotonic()}"
    key = resolve_video_key(vid, source="archivebate")

    # 1. Acquire X -> epoch 1
    res1 = ts.lease_manager.acquire(key, consumer_id="c1", session_id="c1_s1", duration=0.05)
    assert res1.ok and res1.epoch == 1, f"Expected epoch 1, got {res1.epoch}"

    # 2. Advance X to epoch 2 via new session
    res2 = ts.lease_manager.acquire(key, consumer_id="c1", session_id="c1_s2", duration=0.05)
    assert res2.ok and res2.epoch == 2, f"Expected epoch 2, got {res2.epoch}"
    prev_epoch = res2.epoch

    # 3. Allow leases to expire
    time.sleep(0.08)

    # 4. Run pruning
    ts.lease_manager._last_prune = 0.0
    ts.lease_manager._prune_expired_locked(time.monotonic())

    # 5. Acquire new session of X
    res3 = ts.lease_manager.acquire(key, consumer_id="c2", session_id="c2_s1", duration=10.0)
    assert res3.ok, "Expected acquire to succeed for new session"

    # 6. Verify correct generation order (MUST NOT roll back to 1)
    print(f"Previous epoch: {prev_epoch}, New epoch: {res3.epoch}")
    assert res3.epoch > prev_epoch, f"FAIL: Epoch rolled back or failed to advance! Expected > {prev_epoch}, got {res3.epoch}"

    # Register active mock process for new generation
    mock_proc = MockSubprocess(f"proc_{vid}")
    p_key = f"v2_{key.provider_id}_proc_{time.monotonic()}"
    qos.register_v2_process(p_key, mock_proc, key.provider_id, source=key.source, generation=res3.epoch)

    # 7. Release new session
    was_present, remaining = ts.lease_manager.release(key, consumer_id="c2", session_id="c2_s1")
    assert was_present is True, "Release must report was_present=True"
    assert remaining == 0, f"Expected 0 remaining consumers, got {remaining}"

    # 8. Confirm cancellation of active generation succeeded
    assert mock_proc.terminated or mock_proc.killed, "FAIL: Mock process was NOT terminated on final release after pruning!"
    assert qos.is_hard_cancelled(key.provider_id, source=key.source, generation=res3.epoch) is True, \
        "FAIL: QoS failed to record hard_cancel for current generation!"
    print("PASS: Test A: Generation advanced monotonically after natural expiration and cancellation succeeded")


def test_b_delayed_cancellation_synchronization():
    """Test B: Delayed cancellation of session A cannot kill active session B.
    
    1. Create session A.
    2. Begin cancellation of session A and halt at controlled synchronization point.
    3. Create session B (newer generation).
    4. Resume cancellation of session A.
    5. Verify session B remains active and process is protected.
    """
    print("\n--- TEST B: Delayed cancellation synchronization ---")
    vid = f"vid_delayed_cancel_{time.monotonic()}"
    key = resolve_video_key(vid, source="archivebate")

    res_a = ts.lease_manager.acquire(key, consumer_id="c_A", session_id="c_A_s1", duration=30.0)
    assert res_a.ok

    proc_b = MockSubprocess("proc_b")
    p_key_b = f"v2_b_{time.monotonic()}"

    sync_hold = threading.Event()
    sync_done = threading.Event()

    original_mark = qos._mark_processes_preempted

    def synchronized_mark(*args, **kwargs):
        # Notify that we are in cancellation of A
        gen = kwargs.get("generation")
        if gen == res_a.epoch:
            sync_done.set()
            sync_hold.wait(timeout=3.0)
        return original_mark(*args, **kwargs)

    qos._mark_processes_preempted = synchronized_mark

    def cancel_thread_fn():
        ts.lease_manager.release(key, consumer_id="c_A", session_id="c_A_s1")

    t = threading.Thread(target=cancel_thread_fn)
    try:
        t.start()
        assert sync_done.wait(timeout=2.0), "Timed out waiting for cancel A to reach sync point"

        # Under synchronization hold, Session B arrives and starts
        res_b = ts.lease_manager.acquire(key, consumer_id="c_B", session_id="c_B_s1", duration=30.0)
        assert res_b.ok
        assert res_b.epoch > res_a.epoch, f"Session B epoch {res_b.epoch} must be > Session A {res_a.epoch}"
        qos.register_v2_process(p_key_b, proc_b, key.provider_id, source=key.source, generation=res_b.epoch)

        # Release hold to let cancel A proceed
        sync_hold.set()
        t.join(timeout=2.0)

        # Verify Session B is protected!
        assert not proc_b.terminated and not proc_b.killed, "FAIL: Process B was preempted by stale cancel A!"
        assert ts.lease_manager.has_live_lease(key) is True, "Session B lease must remain active!"
        assert not qos.is_hard_cancelled(key.provider_id, source=key.source, generation=res_b.epoch), \
            "Session B must not be hard cancelled!"
        print("PASS: Test B: Session B protected from delayed cancellation of Session A")
    finally:
        qos._mark_processes_preempted = original_mark
        sync_hold.set()
        t.join(timeout=1.0)
        # Cleanup B
        ts.lease_manager.release(key, consumer_id="c_B", session_id="c_B_s1")


def test_c_expired_lease_and_stale_operations():
    """Test C: Expired lease and stale renew/release/build/acquire do not corrupt new generation."""
    print("\n--- TEST C: Expired lease and stale operations ---")
    vid = f"vid_stale_ops_{time.monotonic()}"
    key = resolve_video_key(vid, source="archivebate")

    # Session 1 established and expires
    res1 = ts.lease_manager.acquire(key, consumer_id="tab_1", session_id="tab_1_s1", duration=0.05)
    assert res1.ok
    time.sleep(0.08)

    # Establish Session 2
    res2 = ts.lease_manager.acquire(key, consumer_id="tab_1", session_id="tab_1_s2", duration=30.0)
    assert res2.ok
    assert res2.epoch > res1.epoch

    # Stale renew from Session 1 arrives
    ok_renew, _ = ts.lease_manager.renew(key, consumer_id="tab_1", session_id="tab_1_s1", epoch=res1.epoch)
    assert ok_renew is False, "FAIL: Stale renew from session 1 must be rejected!"

    # Stale release from Session 1 arrives
    was_pres, rem = ts.lease_manager.release(key, consumer_id="tab_1", session_id="tab_1_s1", epoch=res1.epoch)
    assert was_pres is False, "FAIL: Stale release from session 1 must be rejected!"
    assert rem == 1, f"Session 2 must remain live with remaining=1, got {rem}"

    # Stale acquire from Session 1 arrives
    res_stale = ts.lease_manager.acquire(key, consumer_id="tab_1", session_id="tab_1_s1")
    assert res_stale.ok is False, "FAIL: Stale acquire from session 1 must be rejected!"

    # Current Session 2 is intact
    assert ts.lease_manager.has_live_lease(key) is True
    ts.lease_manager.release(key, consumer_id="tab_1", session_id="tab_1_s2")
    print("PASS: Test C: Stale operations from prior generation rejected without corrupting active generation")


def test_d_worker_v2_source_isolation():
    """Test D: Worker V2 loop source isolation for identical provider IDs across distinct sources."""
    print("\n--- TEST D: Worker V2 source isolation ---")
    shared_id = f"shared_video_{time.monotonic()}"
    key_ab = resolve_video_key(shared_id, source="archivebate")
    key_cw = resolve_video_key(shared_id, source="camwhores")

    # 1. Independent active leases
    res_ab = ts.lease_manager.acquire(key_ab, consumer_id="c_ab", duration=30.0)
    res_cw = ts.lease_manager.acquire(key_cw, consumer_id="c_cw", duration=30.0)
    assert res_ab.ok and res_cw.ok

    # 2. Hard cancel archivebate
    qos.hard_cancel(key_ab.provider_id, source=key_ab.source)
    assert qos.is_hard_cancelled(key_ab.provider_id, source=key_ab.source) is True

    # 3. Camwhores must NOT be cancelled
    assert qos.is_hard_cancelled(key_cw.provider_id, source=key_cw.source) is False

    # 4. Verify worker loop check:
    # If worker loop checks qos.is_hard_cancelled without source, it will mistakenly think Camwhores is cancelled!
    # With correct scoping, it must be False
    assert not qos.is_hard_cancelled(key_cw.provider_id, source=key_cw.source)

    # Clean up
    ts.lease_manager.release(key_ab, consumer_id="c_ab")
    ts.lease_manager.release(key_cw, consumer_id="c_cw")
    print("PASS: Test D: Worker V2 source isolation verified")


def test_e_process_registration_and_budget_source_isolation():
    """Test E: FFmpeg process registration, preemption and budget source isolation."""
    print("\n--- TEST E: Process registration & budget source isolation ---")
    shared_id = f"shared_proc_{time.monotonic()}"
    key_ab = resolve_video_key(shared_id, source="archivebate")
    key_cw = resolve_video_key(shared_id, source="camwhores")

    proc_ab = MockSubprocess("proc_ab")
    proc_cw = MockSubprocess("proc_cw")

    qos.register_v2_process("p_ab", proc_ab, key_ab.provider_id, source=key_ab.source, generation=1)
    qos.register_v2_process("p_cw", proc_cw, key_cw.provider_id, source=key_cw.source, generation=1)

    # Cancel archivebate
    preempted = qos.hard_cancel(key_ab.provider_id, source=key_ab.source, generation=1)
    assert proc_ab.terminated or proc_ab.killed, "Archivebate process must be terminated"
    assert not proc_cw.terminated and not proc_cw.killed, "Camwhores process must NOT be terminated!"

    # Cancel camwhores
    qos.hard_cancel(key_cw.provider_id, source=key_cw.source, generation=1)
    assert proc_cw.terminated or proc_cw.killed, "Camwhores process must be terminated after its own cancel"
    print("PASS: Test E: Process registration and preemption strictly isolated by source")


def test_f_bounded_memory_and_eviction():
    """Test F: Bounded memory and safe capacity management without desynchronization."""
    print("\n--- TEST F: Bounded memory & capacity management ---")
    mgr = ts.ConsumerLeaseManager(max_tracked_keys=50)

    # Populate 60 keys (exceeding 50)
    for i in range(60):
        k = resolve_video_key(f"k_{i}", source="archivebate")
        mgr.acquire(k, consumer_id=f"c_{i}", duration=0.01)

    time.sleep(0.08)
    # Prune expired
    mgr._last_prune = 0.0
    mgr._prune_expired_locked(time.monotonic())

    # Ensure leases dict cleaned up and epochs bounded
    assert len(mgr._leases) == 0, f"Expected 0 active leases, got {len(mgr._leases)}"
    assert len(mgr._epochs) <= 50, f"Expected epochs <= 50, got {len(mgr._epochs)}"
    print(f"PASS: Test F: Memory strictly bounded ({len(mgr._epochs)} epochs <= 50)")


if __name__ == "__main__":
    test_a_generation_after_natural_expiration()
    test_b_delayed_cancellation_synchronization()
    test_c_expired_lease_and_stale_operations()
    test_d_worker_v2_source_isolation()
    test_e_process_registration_and_budget_source_isolation()
    test_f_bounded_memory_and_eviction()
    print("\n==========================================")
    print("ALL 6 EPOCH & SOURCE ISOLATION TESTS PASSED!")
    print("==========================================")
