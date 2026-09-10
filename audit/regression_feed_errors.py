"""Source failures stop a stream without claiming a complete catalog."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import feed_service as feed

with patch.object(feed, 'read_json_cache', return_value=(None, None)):
    snap = feed.Snapshot({}, {'a': lambda page: [], 'b': lambda page: []}, lambda rows: rows)
    snap.ended.add('a')
    snap.errors['b'] = 'Temporary HTTP failure'
    result = snap.read(1)
    assert result['complete'] and not result['has_more']
    assert result['retryable'] and result['total_is_estimate']
    snap.errors.clear()
    snap.ended.add('b')
    result = snap.read(1)
    assert result['complete'] and not result['total_is_estimate']
    assert not result['retryable']

print('PASS: source failure is retryable and total remains an estimate')
