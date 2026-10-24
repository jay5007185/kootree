import pandas as pd
import pytest

from scripts.civic_data import (attach_civic, civic_group, load_civic, normalize_claims, normalize_events,
                                normalize_petitions, normalize_status)

NOW = pd.Timestamp('2026-10-24T12:00:00Z')


def claim(identifier, tree, expires):
    return {'id': identifier, 'tree_location_id': tree, 'claimed_at': '2026-10-01T00:00:00Z', 'expires_at': expires,
            'checkin_streak': 0, 'quiz_score': None}


def test_expired_claims_are_dropped():
    frame = normalize_claims([claim('a', 't1', '2027-01-01T00:00:00Z'), claim('b', 't2', '2026-07-01T00:00:00Z')], now=NOW)
    assert frame['tree_id'].tolist() == ['t1']


def test_status_codes_map_to_app_labels_and_reject_unknown():
    rows = [{'tree_location_id': 't1', 'status': 'scheduled_removal', 'reason_category': 'disease', 'unit_name': '公園處',
             'updated_at': '2026-10-24T00:00:00Z', 'pending_since': None, 'watch_count': 3}]
    frame = normalize_status(rows)
    assert frame.iloc[0]['status_label'] == '預定移除'
    assert frame.iloc[0]['reason_label'] == '病害'
    with pytest.raises(ValueError, match='樹況代碼'):
        normalize_status([dict(rows[0], status='mystery')])


def test_events_are_newest_first_with_actor_labels():
    rows = [{'id': 'e1', 'tree_location_id': 't1', 'kind': 'report', 'title': '回報：斷枝倒伏', 'detail': None,
             'actor_kind': 'citizen', 'actor_name': '大軍', 'created_at': '2026-10-24T06:12:25Z', 'source_url': None},
            {'id': 'e2', 'tree_location_id': 't1', 'kind': 'notified', 'title': '系統通知管理單位', 'detail': None,
             'actor_kind': 'system', 'actor_name': None, 'created_at': '2026-10-24T06:13:35Z', 'source_url': None}]
    frame = normalize_events(rows)
    assert frame['id'].tolist() == ['e2', 'e1']
    assert frame['kind_label'].tolist() == ['通知管理單位', '市民回報']
    assert frame['actor_label'].tolist() == ['系統', '市民：大軍']


def test_petition_progress_and_species_summary():
    rows = [{'id': 'p1', 'lat': 25.0, 'lng': 121.5, 'address': '大同區・玉泉里 人行道', 'origin': 'citizen', 'threshold': 50,
             'status': 'open', 'created_at': '2026-10-23T00:00:00Z', 'seed_count': 5, 'species_tally': {'樟樹': 3, '光蠟樹': 2}}]
    frame = normalize_petitions(rows)
    assert frame.iloc[0]['progress'] == pytest.approx(0.1)
    assert frame.iloc[0]['species_summary'] == '樟樹 3、光蠟樹 2'
    assert frame.iloc[0]['status_label'] == '募集中'


def test_attach_civic_marks_trees_without_records_explicitly():
    trees = pd.DataFrame({'tree_id': ['t1', 't2', 't3']})
    civic = {'claims': normalize_claims([claim('a', 't1', '2027-01-01T00:00:00Z')], now=NOW),
             'status': normalize_status([{'tree_location_id': 't2', 'status': 'watching', 'reason_category': None, 'unit_name': None,
                                          'updated_at': '2026-10-24T00:00:00Z', 'pending_since': None, 'watch_count': 2}]),
             'events': normalize_events([]), 'petitions': normalize_petitions([]), 'messages': {}}
    result = attach_civic(trees, civic)
    assert result['claimed'].tolist() == [True, False, False]
    assert result['tree_status'].tolist() == ['無紀錄', '觀察中', '無紀錄']
    assert result['watch_count'].tolist() == [0, 2, 0]
    assert civic_group(result).tolist() == ['認領中', '觀察中', '無紀錄']


def test_load_civic_keeps_other_tables_when_one_fails():
    class Response:
        def __init__(self, rows, status=200):
            self.rows, self.status_code = rows, status
            self.headers = {'Content-Range': f'0-0/{len(rows)}'}

        def json(self):
            return self.rows

    class Client:
        def get(self, url, **kwargs):
            if url.endswith('/tree_status_view'):
                return Response([], 403)
            return Response([])

    env = {'SUPABASE_URL': 'https://example.supabase.co', 'SUPABASE_KEY': 'sb_publishable_test'}
    civic = load_civic(env, Client())
    assert 'HTTP 403' in civic['messages']['status']
    assert civic['messages']['claims'] == 'tree_claims：0 筆'
    assert civic['claims'].empty and civic['status'].empty


def test_ensure_civic_columns_only_fills_missing():
    from scripts.civic_data import ensure_civic_columns
    frame = ensure_civic_columns(pd.DataFrame({'tree_id': ['t1'], 'watch_count': [4]}))
    assert frame.iloc[0]['watch_count'] == 4
    assert frame.iloc[0]['tree_status'] == '無紀錄'
    assert frame.iloc[0]['claimed'] is False or frame.iloc[0]['claimed'] == False
