"""Reproduction script for Session Safety, Double hard_cancel race, and Lease Expiry bugs.

Demonstrates RED on baseline for:
1. Double hard_cancel / redundant cancellation racing with new consumer acquire.
2. Stale release from old session killing newly acquired session of the same consumer.
3. Stale heartbeat renewing an already-expired lease.
4. Source ambiguity: cancel on source A affecting same video ID on source B.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import player_qos_runtime as qos
import timeline_storyboard as ts
from timeline_storyboard import resolve_video_key


def test_reproduce_double_cancel_race():
    """Defect 1: Releasing an unknown or already released consumer must NOT trigger hard_cancel.
    
    If Consumer A releases (dropping consumers to 0, triggering hard_cancel),
    and Consumer B acquires, a second/duplicate release from Consumer A (or endpoint duplicate)
    must NOT trigger hard_cancel on B.
    """
    print("\n--- TEST 1: Double hard_cancel race ---")
    vid = "test_vid_double_cancel"
    key = resolve_video_key(vid, source="archivebate")
    
    # Consumer B acquires
    ts.lease_manager.acquire(key, consumer_id="consumer_B", duration=30.0)
    assert ts.lease_manager.has_live_lease(key) is True
    assert not qos.is_hard_cancelled(key.provider_id)
    
    # A delayed or duplicate release from consumer_A (never registered or already gone) arrives
    was_present, remaining = ts.lease_manager.release(key, consumer_id="consumer_A_stale", reason="stale_delayed")
    
    print(f"Stale release result: was_present={was_present}, remaining={remaining}")
    # On baseline: if consumer_A was not present, remaining is 1 (because B is active).
    # But what if B acquired immediately after A was removed?
    # Let's test:
    # 1. Video with no active consumers
    ts.lease_manager.release(key, consumer_id="consumer_B", reason="cleanup")
    assert qos.is_hard_cancelled(key.provider_id) is True
    
    # 2. Consumer C acquires new session
    ts.lease_manager.acquire(key, consumer_id="consumer_C", duration=30.0)
    assert not qos.is_hard_cancelled(key.provider_id), "Acquire must clear cancel state"
    
    # 3. Duplicate release for consumer_B arrives
    was_present, remaining = ts.lease_manager.release(key, consumer_id="consumer_B", reason="dup_release")
    print(f"Duplicate release of B: was_present={was_present}, remaining={remaining}")
    
    # B was already released! was_present must be False!
    assert was_present is False, f"Duplicate release must report was_present=False, got {was_present}"
    # Consumer C must still be active and not cancelled!
    assert not qos.is_hard_cancelled(key.provider_id), "Duplicate release must NOT cancel active consumer C!"
    assert ts.lease_manager.has_live_lease(key) is True, "Consumer C lease must remain active!"
    print("PASS: Test 1: Duplicate release did not cancel Consumer C")


def test_reproduce_stale_release_same_tab_new_session():
    """Defect 2: Stale release from session A must not kill session B of the same tab/consumer."""
    print("\n--- TEST 2: Stale release from session A vs session B ---")
    vid = "test_vid_sessions"
    key = resolve_video_key(vid, source="archivebate")
    tab_consumer = "tab_browser_1"
    
    # Session A acquired
    ts.lease_manager.acquire(key, consumer_id=tab_consumer, duration=30.0)
    
    # Tab reopens video -> Session B acquired for same tab_consumer
    # If the backend lacks session_id discrimination:
    # A delayed release for Session A arrives.
    # It passes consumer_id=tab_consumer.
    # Without session_id/epoch fencing, it deletes tab_consumer's lease (killing Session B)!
    
    # We test whether release supports session_id fencing:
    try:
        # Acquire Session A with session_id="sess_A"
        ts.lease_manager.acquire(key, consumer_id=tab_consumer, duration=30.0, session_id="sess_A")
        # Acquire Session B with session_id="sess_B"
        ts.lease_manager.acquire(key, consumer_id=tab_consumer, duration=30.0, session_id="sess_B")
        
        # Stale release from sess_A arrives
        was_present, remaining = ts.lease_manager.release(key, consumer_id=tab_consumer, session_id="sess_A")
        print(f"Stale release sess_A: was_present={was_present}, remaining={remaining}")
        
        # sess_A is stale, so was_present should be False and sess_B should remain active!
        assert was_present is False, "Stale session release must be rejected"
        assert remaining == 1, f"Expected 1 active consumer (sess_B), got {remaining}"
        assert ts.lease_manager.has_live_lease(key) is True, "sess_B must remain active"
        print("PASS: Test 2: Session generation fencing protected Session B")
    except TypeError as e:
        print(f"RED: lease_manager does not support session_id fencing yet: {e}")
        raise


def test_reproduce_renew_resurrects_expired_lease():
    """Defect 3: Renewing an expired lease must be rejected and must NOT resurrect it."""
    print("\n--- TEST 3: Renew cannot resurrect expired lease ---")
    vid = "test_vid_expired"
    key = resolve_video_key(vid, source="archivebate")
    cid = "consumer_expiring"
    
    # Acquire very short lease (0.05s)
    ts.lease_manager.acquire(key, consumer_id=cid, duration=0.05)
    assert ts.lease_manager.has_live_lease(key) is True
    
    # Wait for lease to expire
    time.sleep(0.08)
    
    # At this point, monotonic clock > expires!
    # A late heartbeat arrives calling renew():
    ok, count = ts.lease_manager.renew(key, consumer_id=cid, duration=30.0)
    print(f"Renew on expired lease returned: ok={ok}, count={count}")
    
    # On baseline: renew() doesn't check `expires > now` before updating `consumers[cid] = now + dur`!
    # So renew() returns ok=True and resurrects the expired lease!
    assert ok is False, "Renew on expired lease MUST return False!"
    assert count == 0, f"Expected active count 0, got {count}"
    assert ts.lease_manager.has_live_lease(key) is False, "Expired lease must NOT be resurrected by renew"
    print("PASS: Test 3: Renew on expired lease was rejected")


def test_reproduce_source_isolation():
    """Defect 4: Same provider ID on different sources must be completely isolated."""
    print("\n--- TEST 4: VideoKey source isolation ---")
    same_id = "12345"
    key_ab = resolve_video_key(same_id, source="archivebate")
    key_cw = resolve_video_key(same_id, source="camwhores")
    
    assert key_ab.as_string() != key_cw.as_string(), f"Keys must be distinct: {key_ab} vs {key_cw}"
    
    # Acquire lease on both
    ts.lease_manager.acquire(key_ab, consumer_id="c_ab", duration=30.0)
    ts.lease_manager.acquire(key_cw, consumer_id="c_cw", duration=30.0)
    
    assert ts.lease_manager.has_live_lease(key_ab) is True
    assert ts.lease_manager.has_live_lease(key_cw) is True
    
    # Cancel archivebate
    ts.lease_manager.release(key_ab, consumer_id="c_ab", reason="user_close_ab")
    
    # Camwhores MUST still be live and not cancelled!
    assert ts.lease_manager.has_live_lease(key_cw) is True, "Camwhores lease must remain live when archivebate is released"
    assert not qos.is_hard_cancelled(key_cw.as_string()), "Camwhores must not be hard-cancelled by archivebate cancel"
    print("PASS: Test 4: Same provider_id across sources is properly isolated")


if __name__ == "__main__":
    passed = 0
    failed = 0
    for t in [test_reproduce_double_cancel_race, test_reproduce_stale_release_same_tab_new_session, test_reproduce_renew_resurrects_expired_lease, test_reproduce_source_isolation]:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"FAILED: {t.__name__}: {e}")
            failed += 1
    print(f"\nResult: {passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)
