"""Reproduction & Verification tests for Cross-Session Lifecycle, Multi-Tab Cancel, and Stale HTTP Requests.

Documents the RED failure on legacy implementation and proves the GREEN success on the repaired contract.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import player_qos_runtime as qos
import timeline_storyboard as ts


def test_multi_tab_cancel():
    """Defect 1: Closing Tab A must NOT cancel storyboard needed by Tab B."""
    vid = "repro_multi_tab_1"
    # Tab A starts storyboard
    ts.acquire_lease(vid, consumer_id="tab_A", duration=30.0)
    assert ts.has_live_lease(vid) is True

    # Tab B opens the same video in another tab
    ts.acquire_lease(vid, consumer_id="tab_B", duration=30.0)
    assert ts.has_live_lease(vid) is True

    # Tab A closes and invokes release with its consumer_id
    was_present, remaining = ts.release_lease(vid, consumer_id="tab_A", reason="tab_a_pagehide")
    assert was_present is True
    assert remaining == 1

    # On legacy implementation (RED):
    # Lease was wiped out completely and hard_cancel was set globally, killing Tab B's storyboard.
    # On repaired implementation (GREEN):
    # Tab B's lease remains active, hard_cancel is False, and generation continues!
    print(f"[Repro 1] After Tab A cancel: is_hard_cancelled={qos.is_hard_cancelled(vid)}, has_lease={ts.has_live_lease(vid)}")
    assert not qos.is_hard_cancelled(vid), "FAIL: Tab A cancel globally killed video for Tab B!"
    assert ts.has_live_lease(vid) is True, "FAIL: Tab A cancel deleted lease needed by Tab B!"

    # Clean up Tab B
    ts.release_lease(vid, consumer_id="tab_B", reason="tab_b_done")
    assert qos.is_hard_cancelled(vid) is True
    assert not ts.has_live_lease(vid)


def test_stale_get_resurrects_cancel():
    """Defect 2: Stale GET requests must NOT clear hard_cancel after session close."""
    vid = "repro_stale_get_2"
    # Session A starts storyboard
    ts.acquire_lease(vid, consumer_id="session_A", duration=30.0)
    # Session A closes
    ts.release_lease(vid, consumer_id="session_A", reason="session_closed")
    assert qos.is_hard_cancelled(vid) is True

    # Stale GET request arrives with since_rev=0
    # On legacy implementation (RED): record_lease(..., clear_cancel=True) resurrected the cancelled task!
    # On repaired implementation (GREEN): GET /api/storyboard/v2 is read-only manifest query,
    # does not acquire lease or clear cancel.
    manifest = ts.progressive_storyboard_service.get_manifest(vid, 100.0)
    assert manifest is not None

    print(f"[Repro 2] After stale GET: is_hard_cancelled={qos.is_hard_cancelled(vid)}")
    assert qos.is_hard_cancelled(vid) is True, "FAIL: Stale GET resurrected hard_cancel!"
    assert not ts.has_live_lease(vid), "FAIL: Stale GET resurrected lease!"


def test_stale_release_removes_new_session_lease():
    """Defect 3: Stale release from old Session A must NOT delete lease acquired by Session B."""
    vid = "repro_stale_release_3"
    # Session A had a lease
    ts.acquire_lease(vid, consumer_id="session_A", duration=30.0)
    # Session A closed
    ts.release_lease(vid, consumer_id="session_A", reason="close_a")

    # Session B opens and acquires lease
    ts.acquire_lease(vid, consumer_id="session_B", duration=30.0)
    assert ts.has_live_lease(vid) is True

    # Delayed/duplicate release from Session A arrives
    was_present, remaining = ts.release_lease(vid, consumer_id="session_A", reason="delayed_a")
    assert was_present is False
    assert remaining == 1

    # On legacy implementation (RED): remove_lease dropped the entire video lease!
    # On repaired implementation (GREEN): Session B's lease is safe and active!
    print(f"[Repro 3] After stale release: has_live_lease={ts.has_live_lease(vid)}")
    assert ts.has_live_lease(vid) is True, "FAIL: Stale release from Session A removed Session B's lease!"

    # Clean up Session B
    ts.release_lease(vid, consumer_id="session_B", reason="close_b")


if __name__ == "__main__":
    passed = 0
    failed = 0
    for name, fn in [
        ("Multi-tab cancel", test_multi_tab_cancel),
        ("Stale GET clears cancel", test_stale_get_resurrects_cancel),
        ("Stale release drops new lease", test_stale_release_removes_new_session_lease),
    ]:
        print(f"\n--- Testing: {name} ---")
        try:
            fn()
            print(f"RESULT: PASS (GREEN)")
            passed += 1
        except AssertionError as e:
            print(f"RESULT: FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"RESULT: ERROR: {e}")
            failed += 1

    print(f"\nSummary: {passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)
