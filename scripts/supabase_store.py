"""Server-only Supabase snapshot storage; no credentials enter the Dash UI."""
import json
import os
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env', override=False)


def enabled():
    mode = os.getenv('KOOTREE_DATA_SOURCE', 'local')
    if mode not in ('local', 'supabase'):
        raise ValueError('KOOTREE_DATA_SOURCE must be local or supabase')
    return mode == 'supabase'


class Store:
    def __init__(self):
        self.url = os.getenv('SUPABASE_URL', '').rstrip('/')
        key = os.getenv('SUPABASE_SECRET_KEY', '') or os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')
        if not self.url.startswith('https://') or not key:
            raise RuntimeError('Set SUPABASE_URL and a server-only SUPABASE_SECRET_KEY in .env')
        self.headers = {'apikey': key, 'Content-Type': 'application/json'}
        if key.startswith('eyJ'):
            self.headers['Authorization'] = 'Bearer ' + key

    def request(self, method, path, **kwargs):
        try:
            response = requests.request(method, self.url + '/rest/v1/' + path,
                                        headers=self.headers, timeout=120, **kwargs)
        except requests.RequestException:
            raise RuntimeError('Supabase connection failed; check network and project status') from None
        if not response.ok:
            # Do not print response bodies, credentials, or request headers.
            raise RuntimeError(f'Supabase HTTP {response.status_code} at {path}; check schema and server key')
        return response.json() if response.content else None

    def read(self, dataset):
        active = self.request('GET', 'kootree_active_datasets', params={
            'dataset': 'eq.' + dataset, 'select': 'import_id'})
        if not active:
            raise RuntimeError(f'No active Supabase dataset: {dataset}; run import first')
        import_id = active[0]['import_id']
        snapshot = self.request('GET', 'kootree_imports', params={
            'id': 'eq.' + import_id, 'select': 'row_count,metadata'})[0]
        rows = []
        last_ordinal = None
        while len(rows) < snapshot['row_count']:
            # Keyset pagination on ordinal: OFFSET makes Postgres walk every skipped row
            # again on each page, which is what burns Supabase disk I/O on 90k-row snapshots.
            params = {'import_id': 'eq.' + import_id, 'select': 'ordinal,payload',
                      'order': 'ordinal.asc', 'limit': 1000}
            if last_ordinal is not None:
                params['ordinal'] = f'gt.{last_ordinal}'
            batch = self.request('GET', 'kootree_rows', params=params)
            if not batch:
                raise RuntimeError(f'Incomplete Supabase dataset: {dataset}')
            last_ordinal = batch[-1]['ordinal']
            rows.extend(row['payload'] for row in batch)
        if len(rows) != snapshot['row_count']:
            raise RuntimeError(f'Supabase row count mismatch: {dataset}')
        return rows, snapshot['metadata']

    def upload(self, dataset, rows, metadata):
        import_id = str(uuid4())
        self.request('POST', 'kootree_imports', json={
            'id': import_id, 'dataset': dataset, 'row_count': len(rows), 'metadata': metadata})
        for start in range(0, len(rows), 500):
            self.request('POST', 'kootree_rows', json=[
                {'import_id': import_id, 'ordinal': i, 'payload': rows[i]}
                for i in range(start, min(start + 500, len(rows)))])
        # One transaction checks the complete batch before switching the active pointer.
        self.request('POST', 'rpc/kootree_activate_import', json={'snapshot_id': import_id})
        return import_id


@lru_cache(maxsize=None)
def read_dataset(dataset):
    """Cache immutable active snapshots for this server process; restart to refresh."""
    return Store().read(dataset)


def load_trees():
    rows, metadata = read_dataset('trees')
    frame = pd.DataFrame(rows)
    required = {'tree_id', 'district', 'road', 'species', 'latitude', 'longitude'}
    if frame.empty or not required.issubset(frame.columns):
        raise RuntimeError('Supabase trees dataset is empty or missing required columns')
    frame['tree_id'] = frame['tree_id'].astype(str)
    for col in ('diameter_cm', 'height_m', 'latitude', 'longitude'):
        frame[col] = pd.to_numeric(frame[col], errors='coerce')
    return frame, metadata['data_mode'] + ' · Supabase'


def import_local():
    store = Store()
    path = ROOT / 'data' / 'trees_clean.csv'
    official = path.exists()
    if not official:
        path = ROOT / 'data' / 'demo_trees.csv'
    frame = pd.read_csv(path, dtype={'tree_id': str}, low_memory=False)
    if frame.empty:
        raise RuntimeError('Refusing to import an empty tree inventory')
    # Pandas serialization converts NaN to JSON null; preserve row order and duplicate IDs.
    rows = json.loads(frame.to_json(orient='records', force_ascii=False, double_precision=15))
    store.upload('trees', rows, {'data_mode': '官方資料' if official else '虛構示範資料（非官方統計）',
                                'source_file': path.name})
    print(f'Imported trees: {len(rows):,}')
    snapshot = json.loads((ROOT / 'data' / 'treesmatch-clarifications.geojson').read_text(encoding='utf-8'))
    features = snapshot.pop('features')
    store.upload('official_cases', features, snapshot)
    print(f'Imported official clarifications: {len(features):,}')


if __name__ == '__main__':
    import_local()
