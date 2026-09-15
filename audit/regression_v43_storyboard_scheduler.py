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

print('PASS V4.3 STORYBOARD SCHEDULER')
