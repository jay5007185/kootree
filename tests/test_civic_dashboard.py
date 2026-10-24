import importlib
import pandas as pd
import pytest

from scripts.civic_data import attach_civic, normalize_claims, normalize_events, normalize_petitions, normalize_status
from scripts.supabase_data import normalize_reports, normalize_trees, attach_report_summary


@pytest.fixture
def dashboard(monkeypatch):
    import scripts.supabase_data as source
    import scripts.care_calendar as care
    import scripts.civic_data as civic_module
    trees = normalize_trees([
        {'tree_id': 't1', 'tree_kind': '行道樹', 'district': '大安區', 'road': '測試路', 'species': '榕樹', 'latitude': 25.03, 'longitude': 121.5},
        {'tree_id': 't2', 'tree_kind': '受保護樹木', 'district': '大同區', 'road': '未提供', 'species': '雀榕', 'latitude': 25.05, 'longitude': 121.51},
    ])
    civic = {
        'claims': normalize_claims([{'id': 'c1', 'tree_location_id': 't1', 'claimed_at': '2026-10-24T05:00:00Z', 'expires_at': '2099-01-01T00:00:00Z', 'checkin_streak': 1, 'quiz_score': None}]),
        'status': normalize_status([{'tree_location_id': 't2', 'status': 'watching', 'reason_category': None, 'unit_name': None, 'updated_at': '2026-10-24T06:12:25Z', 'pending_since': None, 'watch_count': 3}]),
        'events': normalize_events([{'id': 'e1', 'tree_location_id': 't2', 'kind': 'report', 'title': '回報：斷枝倒伏', 'detail': '倒了', 'actor_kind': 'citizen', 'actor_name': '大軍', 'created_at': '2026-10-24T06:12:25Z', 'source_url': None}]),
        'petitions': normalize_petitions([{'id': 'p1', 'lat': 25.0, 'lng': 121.5, 'address': '大同區・玉泉里 人行道', 'origin': 'citizen', 'threshold': 50, 'status': 'open', 'created_at': '2026-10-23T00:00:00Z', 'seed_count': 1, 'species_tally': {'樟樹': 1}}]),
        'messages': {},
    }
    monkeypatch.setattr(source, 'load_demo_reports', lambda: source.normalize_demo_reports([]))
    monkeypatch.setattr(care, 'load_care_calendar', lambda: {})
    monkeypatch.setattr(source, 'load_trees', lambda: trees)
    monkeypatch.setattr(source, 'load_reports', lambda: normalize_reports([]))
    monkeypatch.setattr(civic_module, 'load_civic', lambda: civic)
    app = importlib.import_module('dashboard_supabase')
    monkeypatch.setattr(app, 'trees', attach_civic(attach_report_summary(trees, normalize_reports([])), civic))
    monkeypatch.setattr(app, 'CIVIC', civic)
    monkeypatch.setattr(app, 'CIVIC_READY', True)
    monkeypatch.setattr(app, 'reports_ready', True)
    return app


def test_civic_tiles_and_tables_follow_district_filter(dashboard):
    filters = ('all', 'all', 'all', 'all', 'all', 10, [], {}, 'real')
    claims, watch, unhealthy, petitions, summary, rows, columns = dashboard.render_civic('status', *filters)
    assert (claims, watch, unhealthy, petitions) == ('1', '3', '1', '1')
    assert rows[0]['tree_code'] == '未提供' and rows[0]['status_label'] == '觀察中' and rows[0]['tree_kind'] == '受保護樹木'
    assert rows[0]['updated_at'] == '2026-10-24 14:12'  # Asia/Taipei
    claims, watch, unhealthy, petitions, _, rows, _ = dashboard.render_civic('events', '大安區', *filters[1:])
    assert (claims, watch, unhealthy, petitions) == ('1', '0', '0', '0')
    assert rows == []
    _, _, _, _, _, rows, _ = dashboard.render_civic('petitions', '大同區', *filters[1:])
    assert rows[0]['species_summary'] == '樟樹 1'


def test_civic_map_layer_groups_and_detail_panel(dashboard):
    figure = dashboard.update_dashboard('all', 'all', 'all', 'all', 'all', 10, [], 'civic', {})[0]
    names = {trace.name for trace in figure.data}
    assert {'觀察中', '認領中'} <= names and '無紀錄' not in names
    detail = dashboard.show_tree({'points': [{'customdata': ['t2']}]})
    labels = {child.children[0].children: child.children[1].children for child in detail.children}
    assert labels['樹況（App）'] == '觀察中' and labels['集氣票數'] == '3' and labels['樹靈認領'] == '無人認領' and labels['樹木類型'] == '受保護樹木'
