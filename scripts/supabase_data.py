"""Read dashboard data from Supabase's REST API; never fall back to local data."""
import os
import re
from urllib.parse import urlparse

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

REQUIRED = ['tree_id', 'district', 'road', 'species', 'latitude', 'longitude']
OPTIONAL_TEXT = ['tree_code', 'tree_kind', 'survey_date', 'issue_type', 'issue_status', 'risk_level', 'tagged_date', 'source_created_at']
OPTIONAL_NUMBER = ['diameter', 'height', 'diameter_cm', 'height_m', 'risk_score', 'case_age_days']
PAGE_SIZE = 1000
TAIPEI_DISTRICTS = '中正區|大同區|中山區|松山區|大安區|萬華區|信義區|士林區|北投區|內湖區|南港區|文山區'
# tree_locations 一張表裝兩種樹：公園處行道樹與文化局受保護樹木。
TREE_TYPES = {'street': '行道樹', 'protected': '受保護樹木'}
# 樹靈記 App（PlantyFun）寫入 tree_reports.type 的代碼；未知代碼原樣顯示。
REPORT_TYPE_LABELS = {
    'disease_sign': '疑似病徵',
    'broken_or_fallen': '斷枝倒伏',
    'cut_or_missing': '被砍或消失',
    'tag_lost': '樹牌遺失',
    'newly_planted': '新植',
    'other': '其他',
}


def read_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=['GET'], raise_on_status=False)
    session.mount('https://', HTTPAdapter(max_retries=retry))
    return session


def map_tree_locations(rows):
    """Adapt the existing public.tree_locations schema to dashboard columns."""
    mapped = []
    for row in rows:
        missing = {'id', 'species_name', 'lat', 'lng'} - row.keys()
        if missing:
            raise ValueError('Supabase tree_locations 缺少欄位：' + ', '.join(sorted(missing)))
        district = None
        for field in ('division_name', 'address'):
            match = re.search(TAIPEI_DISTRICTS, str(row.get(field) or ''))
            if match:
                district = match.group()
                break
        mapped.append({
            'tree_id': row['id'],
            'tree_code': row.get('code'),
            'tree_kind': TREE_TYPES.get(row.get('tree_type'), row.get('tree_type')),
            'district': district,
            'road': row.get('region'),
            'species': row['species_name'],
            'latitude': row['lat'],
            'longitude': row['lng'],
            'height': row.get('height'),
            'diameter': row.get('diameter'),
            'tagged_date': row.get('hang_tag_date'),
            'source_created_at': row.get('created_at'),
        })
    # Preserve source measurements without guessing units. Database creation and
    # tagging dates are separate from an official tree survey date.
    return mapped


def empty_trees():
    return pd.DataFrame(columns=REQUIRED + OPTIONAL_TEXT + OPTIONAL_NUMBER)


def normalize_trees(rows):
    if not rows:
        return empty_trees()
    df = pd.DataFrame(rows)
    missing = sorted(set(REQUIRED) - set(df.columns))
    if missing:
        raise ValueError('Supabase 樹木資料缺少欄位：' + ', '.join(missing))
    if df['tree_id'].isna().any() or df['tree_id'].astype(str).str.strip().eq('').any():
        raise ValueError('Supabase tree_id 不可為空')
    df['tree_id'] = df['tree_id'].astype(str)
    if df['tree_id'].duplicated().any():
        raise ValueError('Supabase tree_id 必須唯一，請提供每棵樹一筆的資料表或 view')
    for column in OPTIONAL_TEXT:
        if column not in df:
            df[column] = None
    for column in OPTIONAL_NUMBER + ['latitude', 'longitude']:
        if column not in df:
            df[column] = None
        df[column] = pd.to_numeric(df[column], errors='coerce')
    if not (df['latitude'].between(-90, 90) & df['longitude'].between(-180, 180)).all():
        raise ValueError('Supabase 經緯度有缺漏或超出有效範圍')
    for column in ['district', 'road', 'species'] + OPTIONAL_TEXT:
        df[column] = df[column].fillna('未提供').astype(str).replace('', '未提供')
    return df


def load_rows(environ=None, session=None, table=None, select="*", order_column="id", filters=None):
    env = os.environ if environ is None else environ
    url = env.get('SUPABASE_URL', '').rstrip('/')
    key = env.get('SUPABASE_KEY', '')
    table = table or env.get('SUPABASE_TREES_TABLE', '')
    if not all([url, key, table]):
        raise ValueError('請設定 SUPABASE_URL、SUPABASE_KEY 與 SUPABASE_TREES_TABLE 後重新啟動')
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('SUPABASE_URL 必須是 HTTPS 專案根網址')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', table):
        raise ValueError('SUPABASE_TREES_TABLE 必須是資料表或 view 名稱')
    schema = env.get('SUPABASE_SCHEMA', 'public')
    headers = {'apikey': key, 'Accept-Profile': schema}
    # New sb_secret / sb_publishable keys are not bearer JWTs.
    if not key.startswith(('sb_secret_', 'sb_publishable_')):
        headers['Authorization'] = f'Bearer {key}'
    client = requests if session is None else session
    rows = []
    total = None
    query = {'select': select, 'order': f'{order_column}.asc', 'limit': PAGE_SIZE}
    query.update(filters or {})
    while total is None or len(rows) < total:
        params = dict(query)
        if rows:
            params[order_column] = f'gt.{rows[-1][order_column]}'
        # Prefer: count=exact 會讓 PostgREST 對整張表多做一次 COUNT；九萬筆的
        # tree_locations 分 94 頁就是 94 次全表計數，吃掉 Supabase 磁碟 I/O 額度。
        # 只在第一頁要總數，之後用 keyset 分頁，最後再核對總筆數。
        page_headers = dict(headers, Prefer='count=exact') if total is None else headers
        try:
            response = client.get(
                f'{url}/rest/v1/{table}', headers=page_headers,
                params=params,
                timeout=30, allow_redirects=False,
            )
        except requests.RequestException:
            raise ValueError('Supabase 連線失敗，請檢查網路與專案設定') from None
        if response.status_code not in (200, 206):
            raise ValueError(f'Supabase 讀取失敗（HTTP {response.status_code}），請檢查資料表、金鑰與讀取權限')
        try:
            page = response.json()
        except ValueError:
            raise ValueError('Supabase 回應不是有效 JSON') from None
        if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
            raise ValueError('Supabase 回應格式錯誤')
        if total is None:
            count = response.headers.get('Content-Range', '').rsplit('/', 1)[-1]
            if not count.isdigit():
                raise ValueError('Supabase 未回傳資料總數，無法確認是否完整讀取')
            total = int(count)
        if not page:
            if len(rows) != total:
                raise ValueError('Supabase 分頁資料不完整')
            break
        rows.extend(page)
        if len(rows) > total:
            raise ValueError('Supabase 資料讀取期間有異動，請重新啟動載入')
    return rows


def load_trees(environ=None, session=None):
    if session is None:
        with read_session() as client:
            return load_trees(environ, client)
    env = os.environ if environ is None else environ
    table = env.get('SUPABASE_TREES_TABLE', '')
    if table == 'tree_locations':
        rows = load_rows(env, session, select='id,code,tree_type,species_name,lat,lng,division_name,address,region,height,diameter,hang_tag_date,created_at',
                         filters={'tree_type': 'in.(' + ','.join(TREE_TYPES) + ')'})
        return normalize_trees(map_tree_locations(rows))
    return normalize_trees(load_rows(env, session, order_column='tree_id'))


REPORT_COLUMNS = ['id', 'tree_id', 'issue_type', 'issue_status', 'created_at', 'case_age_days']


def normalize_reports(rows, now=None):
    if not rows:
        return pd.DataFrame(columns=REPORT_COLUMNS)
    df = pd.DataFrame(rows)
    required = {'id', 'tree_location_id', 'type', 'created_at', 'confirmed_at', 'dismissed'}
    if required - set(df.columns):
        raise ValueError('Supabase 通報資料缺少必要欄位')
    if df['id'].isna().any() or df['id'].duplicated().any() or df['tree_location_id'].isna().any():
        raise ValueError('Supabase 通報編號有缺漏或重複')
    df['tree_id'] = df['tree_location_id'].astype(str)
    raw_type = df['type'].fillna('未分類').replace('', '未分類').astype(str)
    df['issue_type'] = raw_type.map(REPORT_TYPE_LABELS).fillna(raw_type)
    # Confirmation is not closure. No work-order status exists in this schema.
    df['issue_status'] = '未提供'
    df.loc[df['dismissed'].eq(False), 'issue_status'] = '待確認'
    df.loc[df['dismissed'].eq(False) & df['confirmed_at'].notna(), 'issue_status'] = '已確認'
    df.loc[df['dismissed'].eq(True), 'issue_status'] = '已排除'
    created = pd.to_datetime(df['created_at'], errors='coerce', utc=True)
    current = pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now)
    if current.tzinfo is None:
        current = current.tz_localize('UTC')
    age = (current - created).dt.total_seconds() / 86400
    df['case_age_days'] = age.where(age >= 0)
    return df[REPORT_COLUMNS]


def load_reports(environ=None, session=None):
    if session is None:
        with read_session() as client:
            return load_reports(environ, client)
    rows = load_rows(environ, session, table='tree_reports',
                     select='id,tree_location_id,type,created_at,confirmed_at,dismissed')
    return normalize_reports(rows)


def normalize_demo_reports(rows, now=None):
    if any(row.get('is_simulated') is not True for row in rows):
        raise ValueError('模擬資料缺少明確標記，停止載入')
    reports = normalize_reports(rows, now=now)
    if not rows:
        reports['risk_score'] = pd.Series(dtype=float)
        reports['risk_level'] = pd.Series(dtype=str)
        return reports
    raw = pd.DataFrame(rows).set_index('id')
    if not {'risk_score', 'risk_level'}.issubset(raw.columns):
        raise ValueError('模擬風險欄位缺漏')
    scores = pd.to_numeric(reports['id'].map(raw['risk_score']), errors='coerce')
    levels = reports['id'].map(raw['risk_level'])
    expected = scores.map(lambda value: '高' if value >= 70 else '中' if value >= 40 else '低')
    if not scores.between(0, 100).all() or not levels.eq(expected).all():
        raise ValueError('模擬風險分數或分級無效')
    reports['risk_score'] = scores
    reports['risk_level'] = levels
    return reports


def load_demo_reports(environ=None, session=None):
    if session is None:
        with read_session() as client:
            return load_demo_reports(environ, client)
    rows = load_rows(environ, session, table='dashboard_demo_reports',
                     select='id,tree_location_id,type,created_at,confirmed_at,dismissed,is_simulated,risk_score,risk_level')
    return normalize_demo_reports(rows)


def attach_demo_risks(trees, reports):
    result = trees.copy()
    # Multiple simulated reports on a tree: display the highest simulated score.
    scores = reports.groupby('tree_id')['risk_score'].max()
    levels = reports.sort_values('risk_score').drop_duplicates('tree_id', keep='last').set_index('tree_id')['risk_level']
    result['risk_score'] = result['tree_id'].map(scores)
    result['risk_level'] = result['tree_id'].map(levels).fillna('未提供')
    return result


def attach_report_summary(trees, reports):
    result = trees.copy()
    result['issue_type'] = '無通報'
    result['issue_status'] = '無通報'
    result['report_count'] = 0
    for tree_id, group in reports.groupby('tree_id'):
        mask = result['tree_id'].eq(tree_id)
        if not mask.any():
            continue
        result.loc[mask, 'issue_type'] = '、'.join(sorted(group['issue_type'].unique()))
        result.loc[mask, 'issue_status'] = '、'.join(sorted(group['issue_status'].unique()))
        result.loc[mask, 'report_count'] = len(group)
    return result
