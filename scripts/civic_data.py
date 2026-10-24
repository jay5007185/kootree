"""樹靈記 App（PlantyFun）寫入 Supabase 的公民護樹資料：認領、樹況、集氣、時間軸、請願。

這些表由 App 端的遊戲流程寫入，RLS 開放匿名讀取；dashboard 只讀不寫。
任何一張表讀不到就標示缺漏，不推估、不生成假資料。
"""
import pandas as pd

from scripts.supabase_data import load_rows, read_session

STATUS_LABELS = {
    'healthy': '健康',
    'watching': '觀察中',
    'scheduled_removal': '預定移除',
    'removed': '已移除',
    'replanted': '已補植',
}
REASON_LABELS = {
    'disease': '病害', 'hazard': '危木', 'construction': '工程',
    'private_request': '地主申請', 'typhoon': '颱風', 'other': '其他',
}
EVENT_KIND_LABELS = {
    'report': '市民回報', 'report_confirmed': '回報已確認', 'notified': '通知管理單位',
    'status_changed': '樹況變更', 'watching': '列入觀察', 'watch_threshold': '集氣達標',
    'overdue': '單位逾期未回應', 'replant': '補植排程', 'replanted': '已補植',
    'official_clarification': '官方說明', 'proof_uploaded': '上傳證明',
}
ACTOR_LABELS = {'citizen': '市民', 'unit': '管理單位', 'system': '系統'}
PETITION_STATUS_LABELS = {'open': '募集中', 'formed': '已成案', 'submitted': '已送件'}
PETITION_ORIGIN_LABELS = {'removed': '移除後空位', 'citizen': '市民提議'}

CLAIM_COLUMNS = ['id', 'tree_location_id', 'claimed_at', 'expires_at', 'checkin_streak', 'quiz_score']
STATUS_COLUMNS = ['tree_location_id', 'status', 'reason_category', 'unit_name', 'updated_at', 'pending_since', 'watch_count']
EVENT_COLUMNS = ['id', 'tree_location_id', 'kind', 'title', 'detail', 'actor_kind', 'actor_name', 'created_at', 'source_url']
PETITION_COLUMNS = ['id', 'lat', 'lng', 'address', 'origin', 'threshold', 'status', 'created_at', 'seed_count', 'species_tally']


def _frame(rows, columns, key):
    if not rows:
        return pd.DataFrame(columns=columns + ['tree_id'] if 'tree_location_id' in columns else columns)
    frame = pd.DataFrame(rows)
    if set(columns) - set(frame.columns):
        raise ValueError('Supabase 公民護樹資料缺少必要欄位')
    if frame[key].isna().any() or frame[key].duplicated().any():
        raise ValueError('Supabase 公民護樹資料主鍵有缺漏或重複')
    if 'tree_location_id' in columns:
        frame['tree_id'] = frame['tree_location_id'].astype(str)
    return frame


def normalize_claims(rows, now=None):
    """只保留尚未到期的認領；一棵樹同時最多一筆，由資料庫保證。"""
    frame = _frame(rows, CLAIM_COLUMNS, 'id')
    if frame.empty:
        return frame
    current = pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now)
    if current.tzinfo is None:
        current = current.tz_localize('UTC')
    expires = pd.to_datetime(frame['expires_at'], errors='coerce', utc=True)
    if expires.isna().any():
        raise ValueError('Supabase 認領資料缺少到期時間')
    return frame[expires > current].reset_index(drop=True)


def normalize_status(rows):
    frame = _frame(rows, STATUS_COLUMNS, 'tree_location_id')
    if frame.empty:
        frame['status_label'] = pd.Series(dtype=str)
        frame['reason_label'] = pd.Series(dtype=str)
        return frame
    if not frame['status'].isin(STATUS_LABELS).all():
        raise ValueError('Supabase 樹況代碼不在定義範圍')
    frame['status_label'] = frame['status'].map(STATUS_LABELS)
    reason = frame['reason_category'].astype(object)
    frame['reason_label'] = reason.map(REASON_LABELS).fillna(reason).fillna('')
    frame['watch_count'] = pd.to_numeric(frame['watch_count'], errors='coerce').fillna(0).astype(int)
    return frame


def normalize_events(rows):
    frame = _frame(rows, EVENT_COLUMNS, 'id')
    if frame.empty:
        frame['kind_label'] = pd.Series(dtype=str)
        frame['actor_label'] = pd.Series(dtype=str)
        return frame
    kind = frame['kind'].astype(str)
    frame['kind_label'] = kind.map(EVENT_KIND_LABELS).fillna(kind)
    actor_kind = frame['actor_kind'].astype(object).map(ACTOR_LABELS).fillna('')
    actor_name = frame['actor_name'].astype(object).fillna('')
    frame['actor_label'] = [f'{kind_}：{name}' if name else kind_ for kind_, name in zip(actor_kind, actor_name)]
    frame['created_at'] = pd.to_datetime(frame['created_at'], errors='coerce', utc=True)
    return frame.sort_values('created_at', ascending=False).reset_index(drop=True)


def normalize_petitions(rows):
    frame = _frame(rows, PETITION_COLUMNS, 'id')
    if frame.empty:
        for column in ('status_label', 'origin_label', 'species_summary'):
            frame[column] = pd.Series(dtype=str)
        frame['progress'] = pd.Series(dtype=float)
        return frame
    status = frame['status'].astype(str)
    origin = frame['origin'].astype(str)
    frame['status_label'] = status.map(PETITION_STATUS_LABELS).fillna(status)
    frame['origin_label'] = origin.map(PETITION_ORIGIN_LABELS).fillna(origin)
    frame['seed_count'] = pd.to_numeric(frame['seed_count'], errors='coerce').fillna(0).astype(int)
    frame['threshold'] = pd.to_numeric(frame['threshold'], errors='coerce')
    frame['progress'] = (frame['seed_count'] / frame['threshold']).where(frame['threshold'] > 0)
    frame['species_summary'] = frame['species_tally'].map(
        lambda tally: '、'.join(f'{name} {count}' for name, count in sorted((tally or {}).items(), key=lambda item: -item[1])) or '尚未指定樹種'
        if isinstance(tally, dict) else '尚未指定樹種')
    return frame


def empty_civic():
    return {'claims': normalize_claims([]), 'status': normalize_status([]), 'events': normalize_events([]),
            'petitions': normalize_petitions([]), 'messages': {}}


def load_civic(environ=None, session=None):
    """每張表各自載入；單張失敗只影響該表，訊息留在 messages 給畫面顯示。"""
    if session is None:
        with read_session() as client:
            return load_civic(environ, client)
    result = empty_civic()
    sources = [
        ('claims', 'tree_claims', CLAIM_COLUMNS, 'id', normalize_claims),
        ('status', 'tree_status_view', STATUS_COLUMNS, 'tree_location_id', normalize_status),
        ('events', 'tree_events', EVENT_COLUMNS, 'id', normalize_events),
        ('petitions', 'petition_slot_summary', PETITION_COLUMNS, 'id', normalize_petitions),
    ]
    for name, table, columns, order_column, normalize in sources:
        try:
            rows = load_rows(environ, session, table=table, select=','.join(columns), order_column=order_column)
            result[name] = normalize(rows)
            result['messages'][name] = f'{table}：{len(result[name]):,} 筆'
        except ValueError as error:
            result['messages'][name] = f'{table} 尚未提供可讀取資料（{error}）'
    return result


def attach_civic(trees, civic):
    """把每棵樹的認領、樹況、集氣數掛到樹籍上；沒有紀錄的樹明確標示。"""
    result = trees.copy()
    claims = civic['claims']
    status = civic['status']
    claimed = set(claims['tree_id']) if len(claims) else set()
    result['claimed'] = result['tree_id'].isin(claimed)
    status_map = status.set_index('tree_id') if len(status) else pd.DataFrame(columns=['status_label', 'watch_count'])
    result['tree_status'] = result['tree_id'].map(status_map['status_label']).fillna('無紀錄')
    result['watch_count'] = result['tree_id'].map(status_map['watch_count']).fillna(0).astype(int)
    return result


CIVIC_DEFAULTS = {'tree_kind': '未提供', 'claimed': False, 'tree_status': '無紀錄', 'watch_count': 0}


def ensure_civic_columns(frame):
    """樹籍若還沒掛上公民護樹欄位（例如尚未載入），補上明確的「無紀錄」預設值。"""
    missing = {column: value for column, value in CIVIC_DEFAULTS.items() if column not in frame}
    return frame.assign(**missing) if missing else frame


def civic_group(frame):
    """地圖「護樹狀態」圖層的分組：樹況優先，其次集氣與認領。"""
    group = pd.Series('無紀錄', index=frame.index, dtype=object)
    group[frame['claimed']] = '認領中'
    group[frame['watch_count'] > 0] = '集氣中'
    for label in ('已補植', '觀察中', '預定移除', '已移除'):
        group[frame['tree_status'].eq(label)] = label
    return group
