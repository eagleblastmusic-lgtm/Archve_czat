import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import storyboard_service as story
import fast_storyboard_quick as quick
import player_qos_runtime as qos_layer

quick.install()
qos_layer._install_scheduler_qos(quick)


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

# 1. Runtime contract: exact-segment-first, bounded global FFmpeg budget, no FULL auto-upgrade.
stats = story.runtime_stats()
assert stats['workers'] == 2, stats
assert stats['ffmpeg_max_concurrent'] == 2, stats
assert stats['auto_full_upgrade'] is False, stats
assert stats['storyboard_version'] >= 8, stats
print('PASS 1: two-worker scheduler + two-process global FFmpeg budget are active')

# 2. Urgent target generation supersedes an older urgent target before FFmpeg.
with story._state_lock:
    story._desired_segments.clear()
first_generation = story._set_desired_segment('v43-fixture', 2)
second_generation = story._set_desired_segment('v43-fixture', 15)
assert second_generation > first_generation
assert story._job_is_superseded('v43-fixture', 2, first_generation, 0) is True
assert story._job_is_superseded('v43-fixture', 15, second_generation, 0) is False
print('PASS 2: stale urgent targets are rejected by generation before decoding')

# 3. New urgent target preempts stale exact work, but persistent QUICK is not target-owned.
old_proc = FakeProc()
keep_proc = FakeProc()
quick_proc = FakeProc()
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
    story._active_processes['quick'] = {
        'proc': quick_proc,
        'video_key': story._key('preempt-fixture'),
        'kind': 'quick',
        'segment': None,
        'priority': 3,
    }
story._preempt_active_for_target('preempt-fixture', 9)
assert old_proc.terminated == 1, old_proc.terminated
assert keep_proc.terminated == 0, keep_proc.terminated
assert quick_proc.terminated == 0, quick_proc.terminated
with story._active_process_lock:
    story._active_processes.clear()
    story._preempted_processes.clear()
print('PASS 3: target churn preempts stale exact work without killing shared QUICK')

# 4. Last-consumer release is transient: segment work dies, durable QUICK survives.
segment_proc = FakeProc()
quick_proc = FakeProc()
story.demand('lease-fixture', 'consumer-a', active=True)
with story._active_process_lock:
    story._active_processes['lease-segment'] = {
        'proc': segment_proc,
        'video_key': story._key('lease-fixture'),
        'kind': 'segment',
        'segment': 0,
        'priority': 0,
    }
    story._active_processes['lease-quick'] = {
        'proc': quick_proc,
        'video_key': story._key('lease-fixture'),
        'kind': 'quick',
        'segment': None,
        'priority': 3,
    }
story.demand('lease-fixture', 'consumer-a', active=False)
assert segment_proc.terminated == 1, segment_proc.terminated
assert quick_proc.terminated == 0, quick_proc.terminated
with story._active_process_lock:
    story._active_processes.clear()
    story._preempted_processes.clear()
print('PASS 4: transient lease release preserves QUICK but cancels target-specific segment work')

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
while story._jobs.qsize() > queued_before:
    story._jobs.get_nowait()
    story._jobs.task_done()
story.demand('dedupe-fixture', 'consumer', active=False)
with story._state_lock:
    story._states.clear()
    story._desired_segments.clear()
print('PASS 5: identical in-flight segment requests are deduplicated')

# 6. Background work is restricted to the current target or predicted neighbor.
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
print('PASS 6: background work is limited to one directional neighbor')

# 7. QUICK remains durable across exact target churn but obeys explicit hard cancellation.
assert quick.QUICK_FRAME_COUNT == 4
assert quick.QUICK_PARALLELISM == 2
assert quick.QUICK_MIN_SUCCESS == 3
quick_stats = quick.runtime_stats()
assert quick_stats['exact_preempts_quick'] is False, quick_stats
assert getattr(story, '_v43_playback_safe_quick_installed', False) is True

story.demand('quick-priority-fixture', 'coarse-consumer', active=True)
with story._state_lock:
    story._desired_segments.clear()
assert quick._cancel_requested('quick-priority-fixture') is False
story._set_desired_segment('quick-priority-fixture', 5)
assert quick._cancel_requested('quick-priority-fixture') is False
story.hard_cancel('quick-priority-fixture', reason='test_switch')
assert quick._cancel_requested('quick-priority-fixture') is True
story.demand('quick-priority-fixture', 'coarse-consumer', active=True)
assert quick._cancel_requested('quick-priority-fixture') is False
story.demand('quick-priority-fixture', 'coarse-consumer', active=False)
with story._state_lock:
    story._desired_segments.clear()
print('PASS 7: QUICK ignores target churn but obeys real lifecycle hard-cancel')

# 8. Soft playback protection preempts both exact and QUICK without setting hard-cancel poison.
soft_quick = FakeProc()
soft_exact = FakeProc()
with story._active_process_lock:
    story._active_processes.clear()
    story._preempted_processes.clear()
    story._active_processes['soft-quick'] = {
        'proc': soft_quick,
        'video_key': story._key('soft-fixture'),
        'kind': 'quick',
        'segment': None,
        'priority': 3,
    }
    story._active_processes['soft-exact'] = {
        'proc': soft_exact,
        'video_key': story._key('soft-fixture'),
        'kind': 'segment',
        'segment': 1,
        'priority': 0,
    }
cancelled = story.protect_playback('test_low_buffer')
assert cancelled == 2, cancelled
assert soft_quick.terminated == 1 and soft_exact.terminated == 1
assert story._hard_cancel_requested('soft-fixture') is False
with story._active_process_lock:
    story._active_processes.clear()
    story._preempted_processes.clear()
print('PASS 8: playback protection kills active storyboard decoders without poisoning recovery')

# 9. QoS policy: critical playback blocks everything; healthy playback serializes but allows urgent exact.
qos = {'active': True, 'paused': False, 'seeking': True, 'buffered_seconds': 8.0, 'ready_state': 4, 'is_busy': True}
story.configure_playback_qos(lambda: qos)
assert story._qos_blocks_new_process('segment', 0) is True
assert story._qos_blocks_new_process('quick', 3) is True
qos.update(seeking=False, buffered_seconds=3.0, ready_state=4, is_busy=True)
assert story._qos_blocks_new_process('segment', 0) is False
assert story._qos_blocks_new_process('quick', 3) is True
qos.update(buffered_seconds=9.0, is_busy=False)
assert story._qos_blocks_new_process('segment', 1) is False
assert story._qos_blocks_new_process('quick', 3) is False
qos.update(paused=True, buffered_seconds=0.0, ready_state=2, is_busy=True)
assert story._qos_blocks_new_process('quick', 3) is False
story.configure_playback_qos(None)
print('PASS 9: player QoS blocks critical work, protects QUICK thresholds, and allows paused generation')

print('PASS V4.5.2 STORYBOARD SCHEDULER + PLAYER QOS')
