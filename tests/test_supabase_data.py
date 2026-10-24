import pytest
import requests

from scripts.supabase_data import load_trees, normalize_trees


ENV = {'SUPABASE_URL': 'https://example.supabase.co', 'SUPABASE_KEY': 'sb_secret_test', 'SUPABASE_TREES_TABLE': 'trees'}


def tree(tree_id):
    return {'tree_id': tree_id, 'district': '中正區', 'road': '測試路', 'species': '榕樹', 'latitude': 25.03, 'longitude': 121.5}


class Response:
    status_code = 200

    def __init__(self, rows, total):
        self.rows = rows
        self.headers = {'Content-Range': f'0-1/{total}'}

    def json(self):
        return self.rows


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


def test_missing_configuration_does_not_fall_back_to_csv():
    with pytest.raises(ValueError, match='請設定'):
        load_trees({})


def test_pagination_handles_server_page_limit_and_no_synthetic_cases():
    client = Client([Response([tree('1'), tree('2')], 3), Response([tree('3')], 1)])
    data = load_trees(ENV, client)
    assert data['tree_id'].tolist() == ['1', '2', '3']
    assert client.calls[1][1]['params']['tree_id'] == 'gt.2'
    assert 'Authorization' not in client.calls[0][1]['headers']
    # 只有第一頁要 count=exact；每頁都要會讓 PostgREST 重複全表計數，耗 Supabase I/O 額度。
    assert client.calls[0][1]['headers']['Prefer'] == 'count=exact'
    assert 'Prefer' not in client.calls[1][1]['headers']
    assert data['issue_type'].eq('未提供').all()
    assert data['risk_score'].isna().all()


def test_legacy_key_uses_bearer():
    client = Client([Response([], 0)])
    load_trees(dict(ENV, SUPABASE_KEY='legacy-jwt'), client)
    assert client.calls[0][1]['headers']['Authorization'] == 'Bearer legacy-jwt'


def test_permission_error_does_not_leak_key_or_return_local_data():
    response = Response([], 0)
    response.status_code = 403
    with pytest.raises(ValueError, match='HTTP 403') as error:
        load_trees(ENV, Client([response]))
    assert ENV['SUPABASE_KEY'] not in str(error.value)


def test_truncated_data_is_rejected():
    with pytest.raises(ValueError, match='不完整'):
        load_trees(ENV, Client([Response([tree('1')], 2), Response([], 1)]))


def test_invalid_coordinates_and_duplicate_ids_are_rejected():
    with pytest.raises(ValueError, match='經緯度'):
        normalize_trees([dict(tree('1'), latitude=None)])
    with pytest.raises(ValueError, match='唯一'):
        normalize_trees([tree('1'), tree('1')])


def test_network_error_hides_connection_details():
    class Offline:
        def get(self, *args, **kwargs):
            raise requests.ConnectionError('sensitive request details')
    with pytest.raises(ValueError, match='連線失敗') as error:
        load_trees(ENV, Offline())
    assert 'sensitive' not in str(error.value)


def location(identifier, **overrides):
    return dict({'id': identifier, 'code': 'BT001', 'species_name': '榕樹',
                 'lat': '25.03', 'lng': '121.5', 'division_name': '士林區',
                 'region': '文林路', 'height': 16, 'diameter': 54}, **overrides)


def test_tree_locations_maps_schema_and_paginates_by_uuid():
    client = Client([Response([location('uuid-1')], 2),
                     Response([location('uuid-2')], 1)])
    data = load_trees(dict(ENV, SUPABASE_TREES_TABLE='tree_locations', SUPABASE_SCHEMA='public'), client)
    assert data['tree_id'].tolist() == ['uuid-1', 'uuid-2']
    assert data['district'].tolist() == ['士林區', '士林區']
    assert data['road'].eq('文林路').all()
    assert data['species'].eq('榕樹').all()
    assert data['latitude'].eq(25.03).all()
    assert data['longitude'].eq(121.5).all()
    assert data['height_m'].isna().all()
    assert data['diameter_cm'].isna().all()
    assert data['issue_type'].eq('未提供').all()
    assert client.calls[0][0].endswith('/rest/v1/tree_locations')
    assert client.calls[0][1]['params']['order'] == 'id.asc'
    assert client.calls[1][1]['params']['id'] == 'gt.uuid-1'
    assert client.calls[0][1]['params']['tree_type'] == 'in.(street,protected)'
    assert 'tree_type' in client.calls[0][1]['params']['select'].split(',')
    assert 'images' not in client.calls[0][1]['params']['select']
    assert client.calls[0][1]['headers']['Accept-Profile'] == 'public'


def test_tree_locations_district_uses_address_and_does_not_invent_location():
    client = Client([Response([
        location('1', division_name='管理單位', address='臺北市大安區測試路'),
        location('2', division_name='管理單位', region=None),
    ], 2)])
    data = load_trees(dict(ENV, SUPABASE_TREES_TABLE='tree_locations'), client)
    assert data['district'].tolist() == ['大安區', '未提供']
    assert data.iloc[1]['road'] == '未提供'


def test_tree_locations_keeps_coordinate_validation():
    client = Client([Response([location('1', lat=None)], 1)])
    with pytest.raises(ValueError, match='經緯度'):
        load_trees(dict(ENV, SUPABASE_TREES_TABLE='tree_locations'), client)


def test_official_code_and_measurements_are_read_without_replacing_internal_uuid():
    client = Client([Response([location('uuid-1', code='SS0580051129-001', height='3', diameter='5',
                                       created_at='2026-04-02T08:32:19Z', hang_tag_date='2020-01-01')], 1)])
    data = load_trees(dict(ENV, SUPABASE_TREES_TABLE='tree_locations'), client)
    row = data.iloc[0]
    assert row['tree_id'] == 'uuid-1'
    assert row['tree_code'] == 'SS0580051129-001'
    assert row['height'] == 3
    assert row['diameter'] == 5
    assert row['survey_date'] == '未提供'
    assert row['tagged_date'] == '2020-01-01'
    assert row['source_created_at'] == '2026-04-02T08:32:19Z'
    selected = client.calls[0][1]['params']['select'].split(',')
    assert {'id', 'code', 'height', 'diameter'}.issubset(selected)


def test_missing_official_code_does_not_fall_back_to_uuid_or_make_up_measurements():
    client = Client([Response([location('uuid-1', code=None, height=None, diameter=None)], 1)])
    data = load_trees(dict(ENV, SUPABASE_TREES_TABLE='tree_locations'), client)
    assert data.iloc[0]['tree_code'] == '未提供'
    assert data[['height', 'diameter']].isna().all().all()


def test_tree_locations_keeps_both_tree_types_with_labels():
    client = Client([Response([location('s1', tree_type='street'), location('p1', tree_type='protected', region=None)], 2)])
    data = load_trees(dict(ENV, SUPABASE_TREES_TABLE='tree_locations'), client)
    assert data['tree_kind'].tolist() == ['行道樹', '受保護樹木']
    assert data.iloc[1]['road'] == '未提供'


def test_reports_show_app_type_codes_in_chinese_and_keep_unknown_codes():
    from scripts.supabase_data import normalize_reports
    rows = [{'id': 'r1', 'tree_location_id': 't1', 'type': 'disease_sign', 'created_at': '2026-10-24T00:00:00Z', 'confirmed_at': None, 'dismissed': False},
            {'id': 'r2', 'tree_location_id': 't1', 'type': 'broken_or_fallen', 'created_at': '2026-10-24T00:00:00Z', 'confirmed_at': '2026-10-24T01:00:00Z', 'dismissed': False},
            {'id': 'r3', 'tree_location_id': 't2', 'type': 'something_new', 'created_at': '2026-10-24T00:00:00Z', 'confirmed_at': None, 'dismissed': True}]
    reports = normalize_reports(rows)
    assert reports['issue_type'].tolist() == ['疑似病徵', '斷枝倒伏', 'something_new']
    assert reports['issue_status'].tolist() == ['待確認', '已確認', '已排除']
