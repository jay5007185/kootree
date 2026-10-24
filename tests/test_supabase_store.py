import pytest
from scripts.supabase_store import Store


def store(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://example.supabase.co')
    monkeypatch.setenv('SUPABASE_SECRET_KEY', 'sb_secret_test')
    return Store()


def test_read_paginates_by_ordinal_keyset_not_offset(monkeypatch):
    client = store(monkeypatch)
    cursors = []
    def request(method, path, **kwargs):
        if path == 'kootree_active_datasets':
            return [{'import_id': 'snapshot'}]
        if path == 'kootree_imports':
            return [{'row_count': 3, 'metadata': {'data_mode': '官方資料'}}]
        params = kwargs['params']
        assert 'offset' not in params  # OFFSET 分頁每頁都重掃前面的列，吃掉 Supabase I/O 額度
        cursor = params.get('ordinal')
        cursors.append(cursor)
        ordinal = 0 if cursor is None else int(cursor.split('.')[1]) + 1
        return [{'ordinal': ordinal, 'payload': {'tree_id': str(ordinal)}}]
    monkeypatch.setattr(client, 'request', request)
    rows, metadata = client.read('trees')
    assert cursors == [None, 'gt.0', 'gt.1']
    assert [r['tree_id'] for r in rows] == ['0', '1', '2']
    assert metadata['data_mode'] == '官方資料'


def test_incomplete_read_does_not_return_partial_inventory(monkeypatch):
    client = store(monkeypatch)
    def request(method, path, **kwargs):
        return {'kootree_active_datasets': [{'import_id': 'snapshot'}],
                'kootree_imports': [{'row_count': 5, 'metadata': {}}],
                'kootree_rows': []}[path]
    monkeypatch.setattr(client, 'request', request)
    with pytest.raises(RuntimeError, match='Incomplete'):
        client.read('trees')


def test_failed_upload_never_activates_partial_batch(monkeypatch):
    client = store(monkeypatch)
    calls = []
    def request(method, path, **kwargs):
        calls.append(path)
        if path == 'kootree_rows':
            raise RuntimeError('upload failed')
    monkeypatch.setattr(client, 'request', request)
    with pytest.raises(RuntimeError, match='upload failed'):
        client.upload('trees', [{'tree_id': '001'}], {})
    assert 'rpc/kootree_activate_import' not in calls


def test_secret_key_stays_in_headers_and_is_not_a_bearer_jwt(monkeypatch):
    client = store(monkeypatch)
    assert client.headers['apikey'] == 'sb_secret_test'
    assert 'Authorization' not in client.headers
