import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog_service import CatalogService


def video(video_id):
    return {
        "id": str(video_id),
        "url": f"https://www.camwhores.tv/videos/{video_id}/x/",
        "username": "resume_model",
        "date": "1 day ago",
        "source": "camwhores",
        "platform": "Camwhores.tv",
    }


with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "catalog.db"
    service = CatalogService(db)
    conn = service._get_conn()
    now = 1.0
    conn.execute("INSERT INTO revisions(revision,created_at,updated_at,complete,is_active,failed,video_count,error) VALUES(20,?,?,1,1,0,1,NULL)", (now, now))
    conn.execute("INSERT INTO revisions(revision,created_at,updated_at,complete,is_active,failed,video_count,error) VALUES(23,?,?,0,0,1,10,?)", (now, now, json.dumps({"camwhores":"Source did not return a successful response"})))
    conn.execute("INSERT INTO source_runs(revision,source,cursor,pages_scanned,items_found,complete,failed,error,end_reason,updated_at) VALUES(23,'archivebate',1001,1000,100,1,0,NULL,'source_page_limit:empty:1001',?)", (now,))
    conn.execute("INSERT INTO source_runs(revision,source,cursor,pages_scanned,items_found,complete,failed,error,end_reason,updated_at) VALUES(23,'camwhores',461,460,24681,0,1,'Source did not return a successful response',NULL,?)", (now,))

    assert service.get_resumable_revision() == 23

    calls = []
    def archivebate(page):
        raise AssertionError('completed Archivebate source must not be fetched')

    def camwhores(page):
        calls.append(page)
        if page == 461:
            return [video(999001)]
        return []

    rev = service.build_revision_background({'archivebate': archivebate, 'camwhores': camwhores})
    assert rev == 23
    service._indexing_thread.join(timeout=10)
    assert not service._indexing_thread.is_alive()
    assert calls == [461, 462, 463, 464, 465, 466], calls

    row = conn.execute("SELECT complete,is_active,failed,error FROM revisions WHERE revision=23").fetchone()
    assert dict(row) == {'complete': 1, 'is_active': 1, 'failed': 0, 'error': None}, dict(row)
    src = conn.execute("SELECT cursor,pages_scanned,complete,failed,error,end_reason FROM source_runs WHERE revision=23 AND source='camwhores'").fetchone()
    assert int(src['cursor']) == 467, dict(src)
    assert int(src['complete']) == 1 and int(src['failed']) == 0 and src['error'] is None, dict(src)
    assert str(src['end_reason']).startswith('consecutive_empty_pages:5:'), dict(src)
    service.close()

print('PASS: transient Camwhores failure resumes revision from the same durable cursor')
