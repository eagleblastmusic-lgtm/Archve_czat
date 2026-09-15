import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import storyboard_service as story


class FakeProc:
    def __init__(self):
        self.terminated = 0
        self.killed = 0
        self._done = False

    def poll(self):
        return 0 if self._done else None

    def terminate(self):
        self.terminated += 1
        self._done = True

    def kill(self):
        self.killed += 1
        self._done = True


print('--- V4.3 STORYBOARD SCHEDULER REGRESSION ---')

# 1. Runtime contract: exact-segment-first, two workers, no FULL auto-upgrade.
stats = story.runtime_stats()
assert stats['workers'] == 2, stats
assert stats['auto_full_upgrade'] is False, stats
assert stats['storyboard_version'] >= 8, stats
print('PASS 1: two-worker exact-segment scheduler is active and FULL auto-upgrade is disabled')

# 2. Urgent target generation supersedes an older urgent target before FFmpeg.
with story._state_lock:
    story._desired_segments.clear()
first_generation = story._set_desired_segment('v43-fixture', 2)
second_generation = story._set_desired_segment('v43-fixture', 15)
assert second_generation > first_generation
assert story._job_is_superseded('v43-fixture', 2, first_generation, 0) is True
assert story._job_is_superseded('v43-fixture', 15, second_generation, 0) is False
print('PASS 2: stale urgent targets are rejected by generation before decoding')

# 3. New urgent target preempts an active stale process for the same video.
old_proc = FakeProc()
keep_proc = FakeProc()
with story._active_process_lock:
    story._active_processes.clear()
    story._preempted_processes.clear()
    story._active_processes['old'] = {
        'proc': old_proc,
        'video_key': story._key('preempt-fixture'),
        'kind': 'segment',
        'segment': 1,
        'priority': 1,
    }
    story._active_processes['keep'] = {
        'proc': keep_proc,
        'video_key': story._key('preempt-fixture'),
        'kind': 'segment',
        'segment': 9,
        'priority': 0,
    }
story._preempt_active_for_target('preempt-fixture', 9)
assert old_proc.terminated == 1, old_proc.terminated
assert keep_proc.terminated == 0, keep_proc.terminated
with story._active_process_lock:
    story._active_processes.clear()
    story._preempted_processes.clear()
print('PASS 3: stale active FFmpeg is preempted while the current target is preserved')

# 4. Releasing the last consumer terminates active work for that video.
leased_proc = FakeProc()
story.demand('lease-fixture', 'consumer-a', active=True)
with story._active_process_lock:
    story._active_processes['lease-process'] = {
        'proc': leased_proc,
        'video_key': story._key('lease-fixture'),
        'kind': 'segment',
        'segment': 0,
        'priority': 0,
    }
story.demand('lease-fixture', 'consumer-a', active=False)
assert leased_proc.terminated == 1, leased_proc.terminated
with story._active_process_lock:
    story._active_processes.clear()
    story._preempted_processes.clear()
print('PASS 4: last-consumer release cancels active storyboard work')

# 5. Dedupe: repeated requests for the same building segment do not enqueue duplicates.
with story._state_lock:
    story._states.clear()
    story._desired_segments.clear()
story.demand('dedupe-fixture', 'consumer', active=True)
queued_before = story._jobs.qsize()
with patch.object(story, '_cached_segment', return_value=None), patch.object(story, '_ensure_workers', return_value=None):
    one = story.start_segment('dedupe-fixture', 120.0, 3, 'dummy', priority=0)
    after_one = story._jobs.qsize()
    two = story.start_segment('dedupe-fixture', 120.0, 3, 'dummy', priority=0)
    after_two = story._jobs.qsize()
assert one['status'] == 'building'
assert two['status'] == 'building'
assert after_one == queued_before + 1, (queued_before, after_one)
assert after_two == after_one, (after_one, after_two)

# Drain the synthetic queued item without executing it.
while story._jobs.qsize() > queued_before:
    story._jobs.get_nowait()
    story._jobs.task_done()
story.demand('dedupe-fixture', 'consumer', active=False)
with story._state_lock:
    story._states.clear()
    story._desired_segments.clear()
print('PASS 5: identical in-flight segment requests are deduplicated')

# 6. Background work is restricted to the current target or the one predicted
# directional neighbor and is invalidated immediately by a new generation.
with story._state_lock:
    story._desired_segments.clear()
neutral_generation = story._set_desired_segment('direction-fixture', 7)
assert story._job_is_superseded('direction-fixture', 8, neutral_generation, 1) is True
assert story._job_is_superseded('direction-fixture', 7, neutral_generation, 1) is False

story._set_desired_segment('direction-fixture', 6)
forward_generation = story._set_desired_segment('direction-fixture', 7)
assert story._job_is_superseded('direction-fixture', 8, forward_generation, 1) is False
assert story._job_is_superseded('direction-fixture', 9, forward_generation, 1) is True

new_generation = story._set_desired_segment('direction-fixture', 9)
assert new_generation > forward_generation
assert story._job_is_superseded('direction-fixture', 8, forward_generation, 1) is True
print('PASS 6: background work is limited to the predicted neighbor and stale generations are rejected')

# 7. Install the V4.3 QUICK accelerator and verify it reduces speculative work
# and preserves the baseline exact-target preemption semantics.
import fast_storyboard_quick as quick
quick.install()
assert quick.QUICK_FRAME_COUNT == 4
assert quick.QUICK_PARALLELISM == 2
assert quick.QUICK_MIN_SUCCESS == 3
quick_stats = quick.runtime_stats()
assert quick_stats['exact_preempts_quick'] is True, quick_stats
assert getattr(story, '_v43_playback_safe_quick_installed', False) is True

story.demand('quick-priority-fixture', 'coarse-consumer', active=True)
with story._state_lock:
    story._desired_segments.clear()
assert quick._cancel_requested('quick-priority-fixture') is False
story._set_desired_segment('quick-priority-fixture', 5)
assert quick._cancel_requested('quick-priority-fixture') is True
story.demand('quick-priority-fixture', 'coarse-consumer', active=False)
with story._state_lock:
    story._desired_segments.clear()
print('PASS 7: QUICK is capped at 4 frames / 2 workers and yields as soon as exact hover is desired')

# 8. The baseline preemption function must still terminate an active QUICK
# process. The previous V4.3 patch incorrectly protected QUICK and let it compete
# with exact hover + primary playback.
quick_proc = FakeProc()
exact_proc = FakeProc()
with story._active_process_lock:
    story._active_processes.clear()
    story._preempted_processes.clear()
    story._active_processes['quick'] = {
        'proc': quick_proc,
        'video_key': story._key('quick-preempt-fixture'),
        'kind': 'quick',
        'segment': None,
        'priority': 3,
    }
    story._active_processes['exact-current'] = {
        'proc': exact_proc,
        'video_key': story._key('quick-preempt-fixture'),
        'kind': 'segment',
        'segment': 9,
        'priority': 0,
    }
story._preempt_active_for_target('quick-preempt-fixture', 9)
assert quick_proc.terminated == 1, quick_proc.terminated
assert exact_proc.terminated == 0, exact_proc.terminated
with story._active_process_lock:
    story._active_processes.clear()
    story._preempted_processes.clear()
print('PASS 8: exact target preempts active QUICK work and preserves its own process')

print('PASS V4.3 STORYBOARD SCHEDULER + PLAYBACK-SAFE QUICK')
