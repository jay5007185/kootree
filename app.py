"""臺北市智慧行道樹管理 Dashboard | Dash MVP"""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash.exceptions import MissingCallbackContextException
from dash import ALL, Dash, dcc, html, Input, Output, callback, dash_table, ctx, no_update, State

ROOT = Path(__file__).resolve().parent
MAP_STYLE = json.loads((ROOT / 'data' / 'map-style.json').read_text(encoding='utf-8'))
DATA_REAL = ROOT / 'data' / 'trees_clean.csv'
DATA_DEMO = ROOT / 'data' / 'demo_trees.csv'
from scripts.supabase_store import enabled as supabase_enabled, load_trees as load_supabase_trees
if supabase_enabled():
    trees, data_mode = load_supabase_trees()
elif DATA_REAL.exists():
    trees = pd.read_csv(DATA_REAL, dtype={'tree_id':str}, low_memory=False)
    data_mode = '官方資料'
else:
    if not DATA_DEMO.exists():
        from scripts.generate_demo import generate_demo_data
        generate_demo_data().to_csv(DATA_DEMO,index=False,encoding='utf-8-sig')
    trees = pd.read_csv(DATA_DEMO, dtype={'tree_id':str})
    data_mode = '虛構示範資料（非官方統計）'

# 物候規則是示範設定；實務應由植栽專家依地區與物候調整。
CARE_RULES = {
    '木棉': {'months': [3,4,5], 'event': '花果與棉絮觀察（示意窗口）', 'action': '關注果實成熟及棉絮；必要時安排評估'},
    '台灣欒樹': {'months': [9,10,11], 'event': '花果期觀察', 'action': '巡檢落花落果、路面清潔'},
    '小葉欖仁': {'months': [12,1,2], 'event': '換葉期觀察', 'action': '注意落葉清掃及枝條情況'},
    '黑板樹': {'months': [10,11,12], 'event': '花期觀察', 'action': '關注氣味陳情與樹勢'},
}
ISSUE_TYPES = ['落枝枯枝','側枝遮擋','根系隆起','疑似病蟲害','落果清理','種子飛絮','樹木傾斜']
ISSUE_STATUS = ['待巡檢','處理中','已結案']
MONTH_OPTIONS = [{'label':f'{i} 月', 'value':i} for i in range(1,13)]
current_month = pd.Timestamp.now(tz='Asia/Taipei').month
SPECIES_PALETTE = ['#355b3e','#9a7952','#85985d','#b77958','#778c9b','#998394','#70948a','#b49b57','#a86f73','#687e76']
OTHER_SPECIES_COLOR = '#8a948e'

def enrich_decision_demo_fields(df):
    """Add clearly marked synthetic case/risk fields for MVP decision-flow demos."""
    df = df.copy()
    rng = np.random.default_rng(20261020)
    # Frequency and relative weights are demo assumptions, not observed rates.
    has_issue = rng.random(len(df)) < .035
    base_weights = np.array([.24,.23,.17,.14,.08,.05,.09])
    issue_type = np.empty(len(df), dtype=object)
    fluff_species = {'木棉','黑板樹'}
    fruit_species = {'榕樹','雀榕','稜果榕','菩提樹','芒果','芒果樹','福木','欖仁','欖仁樹','大葉欖仁','台灣欒樹'}
    for species in df['species'].unique():
        mask = df['species'].eq(species).to_numpy()
        weights = base_weights.copy()
        weights[4] = weights[4] if species in fruit_species else 0
        weights[5] = weights[5] if species in fluff_species else 0
        issue_type[mask] = rng.choice(ISSUE_TYPES, size=int(mask.sum()), p=weights/weights.sum())
    issue_status = rng.choice(ISSUE_STATUS, size=len(df), p=[.30,.25,.45]).astype(object)
    issue_type[~has_issue] = '無通報'
    issue_status[~has_issue] = '無案件'
    case_age = rng.integers(1, 22, size=len(df))
    case_age[~has_issue] = 0
    diameter = pd.to_numeric(df.get('diameter_cm', 0), errors='coerce').fillna(0)
    height = pd.to_numeric(df.get('height_m', 0), errors='coerce').fillna(0)
    issue_weight = np.where(has_issue, 24, 0)
    structure_weight = np.clip(diameter / 90 * 16 + height / 22 * 10, 0, 26)
    status_weight = np.where(issue_status == '待巡檢', 16, np.where(issue_status == '處理中', 9, 0))
    risk_score = np.clip(np.round(12 + structure_weight + issue_weight + status_weight), 0, 100).astype(int)
    df['issue_type'] = issue_type
    df['issue_status'] = issue_status
    df['case_age_days'] = case_age
    df['risk_score'] = risk_score
    df['risk_level'] = pd.cut(risk_score, bins=[-1,39,69,100], labels=['低','中','高']).astype(str)
    df['decision_fields_note'] = '樹種相容性限制之模擬；3.5%通報率與類型權重為展示假設，非真實發生率；未依季節校準；風險分數非安全診斷'
    return df

# Normalize official/demo spelling before matching care rules and filters.
trees['species'] = trees['species'].astype(str).str.strip().str.replace('臺灣', '台灣', regex=False)
trees = enrich_decision_demo_fields(trees)
import colorsys
SPECIES_COLORS = {}
for index, name in enumerate(trees['species'].value_counts().index):
    rgb = colorsys.hsv_to_rgb((index * .61803398875) % 1, .32, .60)
    SPECIES_COLORS[name] = SPECIES_PALETTE[index] if index < len(SPECIES_PALETTE) else '#%02x%02x%02x' % tuple(round(c*255) for c in rgb)
from scripts.analytics_charts import inventory_chart, issue_chart as build_issue_chart, rate_chart as build_rate_chart, priority_chart, age_chart
from scripts.simulate_operations import generate_operations, monthly_trend, tree_events
operations = generate_operations(trees)

external_stylesheets = ['https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;600;700&family=Noto+Serif+TC:wght@500;600&display=swap']
BRAND_TITLE = '做伙顧樹 KooTree｜城市樹木資訊與公民照護平台'
BRAND_DESCRIPTION = '做伙顧樹，讓樹安好。認識身邊的行道樹，探索城市樹木資訊與照護紀錄。'
app = Dash(__name__, title=BRAND_TITLE, external_stylesheets=external_stylesheets, assets_ignore=r'leaflet.*|MarkerCluster.*', meta_tags=[
    {'name':'description','content':BRAND_DESCRIPTION},
    {'property':'og:title','content':BRAND_TITLE},
    {'property':'og:description','content':BRAND_DESCRIPTION},
    {'property':'og:site_name','content':'做伙顧樹 KooTree'},
    {'property':'og:type','content':'website'},
    {'property':'og:locale','content':'zh_TW'},
    {'name':'twitter:card','content':'summary_large_image'},
    {'name':'twitter:title','content':BRAND_TITLE},
    {'name':'twitter:description','content':BRAND_DESCRIPTION},
])
_brand_index = app.interpolate_index

def brand_index(**kwargs):
    from flask import request
    from html import escape
    # Resolve the image on the current origin, including temporary tunnel URLs.
    origin = request.url_root
    if request.headers.get('X-Forwarded-Proto') == 'https':
        origin = origin.replace('http://', 'https://', 1)
    image_url = escape(origin.rstrip('/') + app.get_asset_url('branding/kootree-brand-ivory-v1.png'), quote=True)
    document = _brand_index(**kwargs)
    return document.replace('</head>', f'<meta property="og:image" content="{image_url}"><meta name="twitter:image" content="{image_url}"></head>')

app.interpolate_index = brand_index
server = app.server

@server.get('/ideas')
def team_ideation_document():
    from flask import send_file
    return send_file(ROOT / 'docs' / '樹咱遊戲化與季節影像發想.html', mimetype='text/html')

from scripts.official_ui import official_section, official_reader, register_official
register_official(app)

@server.after_request
def refresh_theme_assets(response):
    # Revalidate the document and styles so visual updates do not remain cached.
    if response.mimetype in ('text/html', 'text/css'):
        response.headers['Cache-Control'] = 'no-cache, max-age=0, must-revalidate'
    return response


def dropdown(id, label, options, value='all'):
    heading = label
    if id == 'month_filter':
        heading = [html.Span(label), html.Details([
            html.Summary('i', className='info-button', **{'aria-label':'養護觀察月份說明','aria-controls':'month_help'}),
            html.Span([
                html.Strong('養護觀察月份如何使用'),
                html.Span('選擇月份會更新「當月物候關注」數字。勾選「只看該月份建議關注樹種」後，地圖、統計、圖表及明細才會依月份篩選。'),
                html.Span('「養護月份提示」圖層與「全年養護觀察」卡片預設呈現全年規則，不會因單獨切換月份而只顯示當月。其他篩選條件仍會生效。'),
                html.Span('月份來自少量示意物候規則，尚未涵蓋所有樹種；不是即時預警，也不代表該月必須執行養護。'),
                html.Strong('為何先挑這四種樹？'),
                html.Span('本 MVP 以花果、飛絮、換葉與花期氣味等不同觀察情境挑選四個案例，用來展示季節資料如何連動地圖。這不是依官方風險排名、通報量或完整樹種評選產生的名單；未列入的樹種仍需要例行觀察。'),
                html.Strong('各樹種與月份的設定理由'),
                html.Span('木棉｜3–5 月：以春季花果轉換與後續棉絮觀察作為示範情境。官方有 5 月摘果減少飛絮的案例，但不足以驗證臺北一律適用 3–5 月；這段窗口仍屬展示設定。'),
                html.A('參考：新北市木棉摘果案例 ↗',href='https://www.ntpc.gov.tw/ch/home.jsp?dataserno=201805290015&id=28&mcustomize=news_view.jsp&parentpath=0%2C6%2C27',target='_blank',rel='noopener noreferrer'),
                html.Span('台灣欒樹｜9–11 月：選作秋季開花、結果及地面花果觀察案例。林業試驗所記載花期 8–10 月、果期 10–12 月；本系統取其中 9–11 月作示範窗口，並非完整花果期。'),
                html.A('參考：林業試驗所臺灣欒樹物候 ↗',href='https://iesn.tfri.gov.tw/SpeciesContent.aspx?fs=47C8903483394E02&n=7760&sms=0',target='_blank',rel='noopener noreferrer'),
                html.Span('小葉欖仁｜12、1、2 月：選作冬季黃葉、落葉與清掃觀察案例。農業部資料記載冬季黃葉後落葉；本系統將冬季簡化成這三個月，實際換葉時間須依天候與現場調整。'),
                html.A('參考：農業部小葉欖仁介紹 ↗',href='https://kmweb.moa.gov.tw/theme_data.php?id=380&theme=plant_illustration',target='_blank',rel='noopener noreferrer'),
                html.Span('黑板樹｜10–12 月：選作秋冬花期與氣味回報觀察案例。南投環保局記載 11–12 月開花結果及氣味陳情；本系統向前納入 10 月作示範觀察。不同來源花期有差異，不能直接當作臺北固定花期。'),
                html.A('參考：南投環保局黑板樹花期說明 ↗',href='https://www.ntepb.gov.tw/sub/news/Details.aspx?Parser=9%2C20%2C380%2C369%2C%2C%2C14294%2C%2C%2C%2C3',target='_blank',rel='noopener noreferrer'),
                html.Span('使用限制：上述資料提供一般物候或個別地方案例，尚未以臺北逐樹、逐月紀錄校準。正式應用應由在地巡檢與物候紀錄修正窗口；不能據此直接判定危險、病蟲害或安排修剪、用藥。'),

            ],id='month_help',className='info-tooltip'),
        ],className='field-info')]
    return html.Div([html.Div(heading, className='field-label'), dcc.Dropdown(id=id,options=options,value=value,clearable=False)],className='field')

def opts(series):
    return [{'label':'全部','value':'all'}]+[{'label':str(x),'value':str(x)} for x in sorted(series.dropna().astype(str).unique())]

def metric(title, value_id, icon, tone=''):
    icon_name = {'total_trees':'tree', 'open_cases':'report', 'high_risk_count':'risk', 'case_rate':'rate', 'district_count':'place', 'avg_case_age':'time', 'species_count':'leaf', 'care_count':'season'}[value_id]
    children = [html.Div(html.Span(className='botanical-icon', style={'maskImage':f'url({app.get_asset_url("branding/icons/" + icon_name + ".svg")})'}, **{'aria-hidden':'true'}),className='metric-icon'),html.Div(title,className='metric-label'),html.Div(id=value_id,className='metric-value')]
    if value_id in ['open_cases', 'high_risk_count']:
        children.append(html.Small('點選查看模擬明細 ↗'))
        return html.Button(children, id=f'{value_id}_link', n_clicks=0, className=f'metric metric-action {tone}')
    return html.Div(children,className=f'metric {tone}'.strip())

app.layout = html.Div([
    dcc.Store(id='drill_filter', data={}),
    dcc.Store(id='official_overlay', data=[]),
    html.Header([
        html.Div([
            html.Img(src=app.get_asset_url('branding/kootree-logo.png'), alt='KooTree 樹冠與家園標誌', width=80, height=72),
            html.Div([html.H1('做伙顧樹'), html.Div('KooTree', className='c-brand-english'), html.P('做伙顧樹，讓樹安好。')]),
        ], className='c-brand'),
        html.Div(className='c-scene', **{'aria-hidden':'true'}),
        html.Div([html.Span('●',className='online'),html.Span(data_mode)],className='data-badge')
    ],className='masthead-c', **{'aria-label':'做伙顧樹 KooTree｜做伙顧樹，讓樹安好。'}),
    html.Div([
        html.Aside([
            html.Div([html.Div('資料篩選',className='panel-title'),html.P('先選位置，再依樹種、案件或圖層縮小範圍。',className='sidebar-lead')],className='sidebar-head'),
            html.Div([
                html.Div('位置範圍',className='filter-group-title'),
                dropdown('district_filter','行政區',opts(trees['district'])),
                dropdown('road_filter','路段',[{'label':'全部','value':'all'}]),
            ],className='filter-block'),
            html.Div([
                html.Div('資料條件',className='filter-group-title'),
                dropdown('species_filter','樹種',opts(trees['species'])),
                dropdown('issue_filter','通報類型',opts(trees['issue_type'])),
                dropdown('status_filter','案件狀態',opts(trees['issue_status'])),
                dropdown('month_filter','養護觀察月份', MONTH_OPTIONS,current_month),
                dcc.Checklist(id='care_only',options=[{'label':'只看該月份建議關注樹種','value':'care'}],value=[],className='check-option'),
            ],className='filter-block'),
            html.Div([
                html.Div('資料說明',className='filter-group-title'),
                html.P('通報、風險與案件天數為展示決策流程的示範推估，非官方案件紀錄。',className='help-text'),
                html.A('官方資料集 ↗',href='https://data.taipei/dataset/detail?id=7a49d00c-a5ff-4a6b-be9e-aaa6dc1ff7e8',target='_blank',className='source-link'),
            ],className='filter-block subtle-block'),
        ],className='sidebar'),
        html.Main([
            html.Nav([html.A('行道樹總覽',href='#tree-overview'),html.A('樹木與移除／移植地圖',href='#map-workspace')],className='data-navigation', **{'aria-label':'資料區塊導覽'}),
            html.Div([
                metric('符合條件樹木','total_trees','樹','primary'),
                metric('待處理通報','open_cases','件','warning'),
                metric('高風險樹木','high_risk_count','警','danger'),
                metric('每千棵通報','case_rate','率','primary'),
                metric('涵蓋行政區','district_count','區'),
                metric('平均案件天數','avg_case_age','天'),
                metric('樹種數量','species_count','種'),
                metric('當月物候關注','care_count','期')
            ],className='metric-grid metric-grid-wide',id='tree-overview'),
            html.Div([
                html.Section([html.Div([html.Div('樹木分布地圖',className='panel-title'),html.Span('點選樹木，認識身邊的老朋友',className='note')],className='section-heading'),html.Div([
                    dcc.RadioItems(id='map_mode', options=[{'label':'樹種分布','value':'species'},{'label':'養護月份提示','value':'care'},{'label':'通報熱點','value':'issue'},{'label':'風險分級','value':'risk'},{'label':'移除／移植','value':'official'}],value='species',className='map-layer-options',style={'display':'flex','flexWrap':'wrap','gap':'4px'},labelStyle={'display':'inline-flex','alignItems':'center','margin':0},inputStyle={'position':'absolute','opacity':0,'width':'1px','height':'1px'}),
                    html.Button('清除圖卡篩選',id='clear_drill',className='clear-drill-btn map-clear-btn',style={'width':'auto','margin':0}),
                ],className='map-toolbar',style={'display':'flex','alignItems':'center','flexWrap':'wrap','justifyContent':'space-between','gap':'10px'}),html.Div(id='drill_label',className='drill-label muted'),dcc.Graph(id='tree_map',config={'displayModeBar':'hover','displaylogo':False,'scrollZoom':True},style={'height':'540px'}),official_section()],className='surface map-panel'),
                html.Section([
                    dcc.Tabs(id='map_info_tab',value='tree',children=[dcc.Tab(label='樹木資訊',value='tree'),dcc.Tab(label='移除／移植紀錄',value='official')]),
                    html.Div([html.Div('樹木資訊',className='panel-title section-gap'),html.Div('請點選地圖標記，查看該棵樹的資料。',id='tree_details',className='tree-detail'),html.Div('全年養護觀察',className='panel-title section-gap'),html.Div(id='care_list'),html.Div('優先巡檢路段',className='panel-title section-gap'),html.Div(id='priority_roads')],id='tree_info_panel'),
                    html.Div(official_reader(),id='official_info_panel',style={'display':'none'}),
                ],className='surface details-panel')
            ],className='main-grid',id='map-workspace'),
            html.Div([
                html.Section([html.Div('樹種占比 · TOP 10 與其他',className='panel-title'),dcc.Graph(id='species_chart',config={'displayModeBar':False})],className='surface'),
                html.Section([html.Div('行政區樹木數量 · 完整 12 區',className='panel-title'),dcc.Graph(id='district_chart',config={'displayModeBar':False})],className='surface'),
                html.Section([html.Div('模擬通報類型與處理進度',className='panel-title'),html.Details([html.Summary('樹種對應與模擬限制'),html.P('飛絮僅配給木棉、黑板樹；落果清理僅配給已建置果實情境的樹種。未建置不代表不會發生。其餘為通用情境；病蟲害只標示疑似，不推定病原。3.5%通報率、案件狀態與類型權重皆為展示假設，尚未按臺北實際案件或季節校準；不代表樹種風險排名。',className='help-text'),html.A('來源：國土管理署行道樹維護參考',href='https://myway.nlma.gov.tw/upload/sd/15032155602hFM5.pdf',target='_blank'),html.Br(),html.A('來源：臺中市政府黑板樹案例',href='https://www.taichung.gov.tw/8868/8872/9962/987349',target='_blank')],className='chart-method'),dcc.Graph(id='issue_chart',config={'displayModeBar':False})],className='surface'),
                html.Section([html.Div('模擬行政區通報率與整體比較',className='panel-title'),dcc.Graph(id='rate_chart',config={'displayModeBar':False})],className='surface'),
                html.Section([html.Div('模擬路段巡檢優先順序',className='panel-title'),html.P('越往右、上代表待處理與高風險越多；圓點大小代表路段樹木數。再按同一路段可取消篩選。',className='help-text'),dcc.Graph(id='priority_chart',config={'displayModeBar':False})],className='surface'),
                html.Section([html.Div('模擬待處理案件天數',className='panel-title'),html.P('目前模擬案件天數為 1–21 天；分組不代表法定或服務處理期限。',className='help-text'),dcc.Graph(id='age_chart',config={'displayModeBar':False})],className='surface')
            ],className='chart-grid'),
            html.Section([html.Div([html.Div('篩選結果明細',className='panel-title'),html.Button('匯出篩選 CSV',id='export_button',className='export-btn')],className='section-heading'),
                dash_table.DataTable(id='tree_table',columns=[{'name':label,'id':col} for col,label in [('tree_id','樹木編號'),('district','行政區'),('road','路段'),('species','樹種'),('issue_type','通報類型'),('issue_status','案件狀態'),('risk_level','風險'),('risk_score','分數'),('diameter_cm','胸徑'),('height_m','樹高'),('survey_date','調查日期')]],page_size=12,sort_action='native',style_table={'overflowX':'auto'},style_cell={'fontFamily':'Noto Sans TC, sans-serif','textAlign':'left','padding':'11px','border':'none','fontSize':'12px','backgroundColor':'#fffdf7','color':'#2e4534'},style_header={'backgroundColor':'#eeeadd','fontWeight':'bold','color':'#455b52'},style_data_conditional=[{'if':{'row_index':'odd'},'backgroundColor':'#faf8f1'}]),dcc.Download(id='csv_download')],className='surface table-panel'),
            html.Section([
                html.Div('案件與養護資料中心', className='panel-title'),
                html.P('以下案件、日期、承辦班組、巡檢與養護紀錄全部為固定種子產生的模擬資料。歷史趨勢由模擬案件的通報與結案日期統計；與上方位置及資料條件同步。', className='help-text'),
                dcc.Tabs(id='operations_tab', value='cases', children=[
                    dcc.Tab(label='待處理通報', value='cases'),
                    dcc.Tab(label='高風險樹木', value='risk'),
                    dcc.Tab(label='養護工單', value='work'),
                    dcc.Tab(label='歷史趨勢', value='history'),
                ]),
                html.Div(id='operations_summary', className='drill-label active'),
                dcc.Graph(id='history_chart', config={'displayModeBar':False}),
                dash_table.DataTable(id='operations_table', page_size=10, sort_action='native', filter_action='native',
                    style_table={'overflowX':'auto'}, style_cell={'textAlign':'left','padding':'12px','fontFamily':'inherit','minWidth':'100px','backgroundColor':'#fffdf7','color':'#2e4534'},
                    style_header={'backgroundColor':'#eef0e3','fontWeight':'bold'}),
                html.P('點選清單任一列，查看該棵樹的模擬履歷。清單最多顯示 1,000 筆；統計及匯出包含全部符合資料。', className='help-text'),
                html.Button('匯出目前分頁 CSV', id='operations_export', className='export-btn'),
                dcc.Download(id='operations_download'),
                html.Div(id='tree_history', className='tree-detail'),
            ], className='surface table-panel', id='operations'),
            html.Footer([html.Div('做伙顧樹 KooTree · 做伙顧樹，讓樹安好。',className='footer-brand'),html.Span('資料來源：臺北市政府資料大平臺｜通報、風險與案件天數為 MVP 示範推估，非官方案件紀錄、樹木安全診斷或實際養護指示。')],className='footer')
        ],className='content')
    ],className='layout')
],className='page')

@callback(Output('road_filter','options'),Output('road_filter','value'),Input('district_filter','value'))
def update_roads(district):
    df=trees if district=='all' else trees[trees['district']==district]
    return opts(df['road']), 'all'

@callback(Output('drill_filter','data'),Output('species_chart','clickData'),Output('district_chart','clickData'),Output('rate_chart','clickData'),Output('issue_chart','clickData'),Output('priority_chart','clickData'),Input({'type':'care_card','species':ALL},'n_clicks'),Input({'type':'priority_road','district':ALL,'road':ALL},'n_clicks'),Input('issue_chart','clickData'),Input('clear_drill','n_clicks'),Input('district_filter','value'),Input('road_filter','value'),Input('species_filter','value'),Input('issue_filter','value'),Input('status_filter','value'),Input('month_filter','value'),Input('care_only','value'),Input('map_mode','value'),State('drill_filter','data'),Input('priority_chart','clickData'),Input('species_chart','clickData'),Input('species_chart','relayoutData'),State('species_chart','figure'),Input('district_chart','clickData'),Input('rate_chart','clickData'),prevent_initial_call=True)
def update_drill_callback(*args):
    result = update_drill_filter(*args)
    if result is no_update:
        return (no_update,) * 6
    # Clear the consumed event so an identical second click is a new event.
    return result, None, None, None, None, None


def update_drill_filter(care_clicks,road_clicks,issue_click,clear_clicks,district,road,species,issue,status,month,care_only,map_mode,current_drill=None,priority_click=None,species_click=None,species_relayout=None,species_figure=None,district_click=None,rate_click=None):
    trigger = ctx.triggered_id
    def toggle(candidate):
        current = {k:v for k,v in (current_drill or {}).items() if k != 'label'}
        selected = {k:v for k,v in candidate.items() if k != 'label'}
        return {} if selected == current else candidate
    if trigger == 'clear_drill':
        return {}
    if isinstance(trigger, str) and trigger in {'district_filter','road_filter','species_filter','issue_filter','status_filter','month_filter','care_only','map_mode'}:
        return {}
    if isinstance(trigger, dict) and trigger.get('type') == 'care_card':
        species = trigger.get('species')
        return toggle({'type':'species','species':species,'label':f'物候關注樹種：{species}'})
    if isinstance(trigger, dict) and trigger.get('type') == 'priority_road':
        district = trigger.get('district')
        road = trigger.get('road')
        return toggle({'type':'road','district':district,'road':road,'label':f'優先巡檢路段：{district} · {road}'})
    if trigger in ('district_chart','rate_chart'):
        click = district_click if trigger == 'district_chart' else rate_click
        if click:
            selected_district = click['points'][0].get('y')
            if selected_district:
                return toggle({'type':'district','district':selected_district,'label':f'行政區：{selected_district}'})
        return no_update
    if trigger == 'species_chart':
        prop = ctx.triggered[0]['prop_id'].rsplit('.',1)[-1]
        if prop == 'clickData' and species_click:
            label = species_click['points'][0].get('label')
            if label == '其他樹種（合計）':
                labels = (species_figure or {}).get('data',[{}])[0].get('labels',[])
                selected = [s for s in trees['species'].unique() if s not in labels]
                return toggle({'type':'species_set','species':selected,'label':'其他樹種（合計）'})
            if label:
                return toggle({'type':'species','species':label,'label':f'樹種：{label}'})
        if prop == 'relayoutData' and species_relayout and 'hiddenlabels' in species_relayout:
            hidden = species_relayout['hiddenlabels']
            if not hidden:
                return {}
            labels = (species_figure or {}).get('data',[{}])[0].get('labels',[])
            visible = [s for s in labels if s not in hidden]
            selected = [s for s in trees['species'].unique() if s in visible or ('其他樹種（合計）' in visible and s not in labels)]
            return {'type':'species_set','species':selected,'label':'樹種圖例篩選'}
        return no_update
    if trigger == 'priority_chart' and priority_click:
        district,road = priority_click['points'][0]['customdata'][:2]
        return toggle({'type':'road','district':district,'road':road,'label':f'優先巡檢路段：{district} · {road}'})
    if trigger == 'issue_chart' and issue_click:
        issue_type = issue_click['points'][0].get('y')
        if issue_type:
            return toggle({'type':'issue','issue_type':issue_type,'label':f'通報類型：{issue_type}'})
    return no_update

@callback(Output('drill_label','children'),Output('drill_label','className'),Input('drill_filter','data'))
def render_drill_label(drill_filter):
    if not drill_filter:
        return '點選地圖查看樹木；點選圖卡或通報圖表可篩選。','drill-label muted'
    return f"目前鑽取：{drill_filter.get('label','自訂條件')}",'drill-label active'

def filter_data(district,road,species,issue,status,month,care_only):
    df=trees
    for col,value in [('district',district),('road',road),('species',species),('issue_type',issue),('issue_status',status)]:
        if value != 'all':df=df[df[col]==value]
    if 'care' in (care_only or []):
        allowed=[name for name, rule in CARE_RULES.items() if month in rule['months']]
        df=df[df['species'].isin(allowed)]
    return df

def apply_drill_filter(df, drill_filter):
    if not drill_filter:
        return df
    kind = drill_filter.get('type')
    if kind == 'district':
        return df[df['district'] == drill_filter.get('district')]
    if kind == 'species_set':
        return df[df['species'].isin(drill_filter.get('species',[]))]
    if kind == 'species':
        return df[df['species'] == drill_filter.get('species')]
    if kind == 'road':
        return df[(df['district'] == drill_filter.get('district')) & (df['road'] == drill_filter.get('road'))]
    if kind == 'issue':
        return df[df['issue_type'] == drill_filter.get('issue_type')]
    return df

def _official_selection_triggered():
    try:
        return ctx.triggered_id == 'official_case_select'
    except MissingCallbackContextException:
        return False


@callback(Output('tree_map','figure'),Output('total_trees','children'),Output('district_count','children'),Output('open_cases','children'),Output('case_rate','children'),Output('high_risk_count','children'),Output('avg_case_age','children'),Output('species_count','children'),Output('care_count','children'),Output('species_chart','figure'),Output('district_chart','figure'),Output('issue_chart','figure'),Output('rate_chart','figure'),Output('tree_table','data'),Output('care_list','children'),Output('priority_roads','children'),Input('district_filter','value'),Input('road_filter','value'),Input('species_filter','value'),Input('issue_filter','value'),Input('status_filter','value'),Input('month_filter','value'),Input('care_only','value'),Input('map_mode','value'),Input('drill_filter','data'),Input('official_overlay','data'),Input('official_topic','value'),Input('official_query','value'),Input('official_case_select','value'))
def update_dashboard(district,road,species,issue,status,month,care_only,map_mode,drill_filter,official_overlay=None,official_topic='all',official_query='',official_selected=None,map_view=None):
    if map_view:
        try:
            if ctx.triggered_id != 'tree_map':
                map_view = None
            elif not any(k.startswith('mapbox.') for k in map_view):
                map_view = None
        except MissingCallbackContextException:
            pass
    show_official_overlay = map_mode == 'official' or 'official' in (official_overlay or [])
    df=apply_drill_filter(filter_data(district,road,species,issue,status,month,care_only),drill_filter)
    issue_df=df[df['issue_type']!='無通報']
    open_df=df[df['issue_status'].isin(['待巡檢','處理中'])]
    count_care=sum(int((df['species']==s).sum()) for s,r in CARE_RULES.items() if month in r['months'])
    top_species=df['species'].value_counts().head(10).index.tolist()
    color_map={name:SPECIES_COLORS[name] for name in top_species}
    fig=go.Figure()
    from scripts.map_points import select_map_points
    plotted, clusters = select_map_points(df, map_view)
    map_caption = f'目前範圍完整顯示 {len(plotted):,} 棵｜每個點位代表一棵樹'
    fig.add_annotation(text=map_caption,x=0,y=1,xref='paper',yref='paper',xanchor='left',yanchor='top',
                       showarrow=False,bgcolor='#fcfaf3',borderpad=6,font={'size':12,'color':'#294e36'})
    if plotted.empty:
        fig.add_trace(go.Scattermapbox(lat=[], lon=[], mode='markers', showlegend=False))
        fig.add_annotation(text='目前篩選無資料，請清除圖卡篩選或調整條件', x=.5, y=.5, xref='paper', yref='paper', showarrow=False, bgcolor='white', borderpad=12)
    if len(plotted):
        if map_mode=='care':
            plotted=plotted.copy()
            care_labels = {name: f"{name} · {','.join(str(m) for m in rule['months'])} 月" for name, rule in CARE_RULES.items()}
            plotted['map_group'] = plotted['species'].map(care_labels).fillna('其他樹木')
            groups = ['其他樹木'] + list(care_labels.values())
            colors = {'其他樹木':'#77867f', **{label:SPECIES_COLORS.get(name,OTHER_SPECIES_COLOR) for name,label in care_labels.items()}}
        elif map_mode=='issue':
            plotted=plotted.copy()
            plotted['map_group']=plotted['issue_type']
            groups=[g for g in ISSUE_TYPES if g in set(plotted['map_group'])]
            colors={'落枝枯枝':'#a45547','側枝遮擋':'#b77948','根系隆起':'#8c6f34','疑似病蟲害':'#998394','落果清理':'#b49b57','種子飛絮':'#b6a04d','樹木傾斜':'#7c3f33','無通報':'#6b8f80'}
        elif map_mode=='risk':
            plotted=plotted.copy()
            plotted['map_group']=plotted['risk_level']
            groups=['高','中','低']
            colors={'高':'#a45547','中':'#b18a45','低':'#587746'}
        else:
            plotted=plotted.copy()
            plotted['map_group']=plotted['species'].apply(lambda s:s if s in top_species else '其他樹種')
            groups=[name for name in top_species if name in set(plotted['map_group'])]
            if '其他樹種' in set(plotted['map_group']):
                groups.append('其他樹種')
            colors={**color_map,'其他樹種':OTHER_SPECIES_COLOR}
        for group in groups:
            part=plotted[plotted['map_group']==group]
            if part.empty:continue
            custom=part[['tree_id','district','road','species']].fillna('').astype(str).to_numpy()
            fig.add_trace(go.Scattermapbox(lat=part['latitude'],lon=part['longitude'],mode='markers',name=group,visible=True,uid=f'{map_mode}-{group}',
                marker={'size':(5.5 if group=='其他樹木' else 8) if map_mode=='care' else (5 if map_mode=='official' else (10 if group=='高' else 7)),'color':colors.get(group,'#587746'),'opacity':((.48 if group=='其他樹木' else .95) if map_mode=='care' else (.35 if map_mode=='official' else .88))},customdata=custom,
                hovertemplate='<b>%{customdata[3]}</b><br>%{customdata[1]} · %{customdata[2]}<br>ID: %{customdata[0]}<extra></extra>'))
    if show_official_overlay:
        from scripts.official_cases import records_from_geojson, load_snapshot, filter_records
        official_rows = [r for r in filter_records(records_from_geojson(load_snapshot()),official_topic,official_query)
                         if r['latitude'] is not None and r['longitude'] is not None]
        categories = [
            ('removal','原文提及移除','#a45547'),
            ('transplant','原文提及移植','#397c89'),
            ('both','原文提及移除及移植','#80629b'),
            ('other','其他官方說明','#8a9480'),
        ]
        for category,label,color in categories:
            def category_of(record):
                removal=record.get('mentions_removal',False)
                transplant=record.get('mentions_transplant',False)
                return 'both' if removal and transplant else ('removal' if removal else ('transplant' if transplant else 'other'))
            rows = [r for r in official_rows if category_of(r)==category]
            if not rows:
                continue
            fig.add_trace(go.Scattermapbox(
                lat=[r['latitude'] for r in rows], lon=[r['longitude'] for r in rows],
                mode='markers', name=label, uid='official-'+category, legendgroup='official-'+category,
                cluster={'enabled':False},
                marker={'size':11,'color':color,'opacity':.95},
                customdata=[['official:'+r['id'], r['location'], r['published_at'], r['title']] for r in rows],
                hovertemplate='<b>%{customdata[3]}</b><br>%{customdata[1]}<br>%{customdata[2]}｜'+label+'（非執行狀態）<extra></extra>'))
    fig.update_layout(mapbox={'style':MAP_STYLE,'center':{'lat':float(df['latitude'].median()) if len(df) else 25.05,'lon':float(df['longitude'].median()) if len(df) else 121.54},'zoom':12 if district=='all' else 13.3},margin={'l':0,'r':0,'t':0,'b':96},height=540,paper_bgcolor='#fffdf7',legend={'title':{'text':'圖例','font':{'size':12,'color':'#45603f'}},'orientation':'h','y':-.025,'yanchor':'top','x':0,'xanchor':'left','bgcolor':'#f2f3e8','bordercolor':'#e2e6d6','borderwidth':1,'font':{'size':12,'color':'#45603f'},'itemwidth':30,'itemsizing':'constant'},uirevision=f'{district}-{road}',selectionrevision=f'{map_mode}-{month}',legend_uirevision=f'{map_mode}-{month}')
    if show_official_overlay and official_selected:
        chosen = next((r for r in official_rows if r['id'] == official_selected), None)
        if chosen and _official_selection_triggered():
            fig.update_layout(mapbox_center={'lat':chosen['latitude'],'lon':chosen['longitude']},mapbox_zoom=14,
                              uirevision=f'official-{official_selected}')
        if chosen:
            destination_legend_shown = False
            for destination in chosen['destinations']:
                if destination.get('latitude') is None or destination.get('longitude') is None:
                    continue
                fig.add_trace(go.Scattermapbox(lat=[chosen['latitude'],destination['latitude']],lon=[chosen['longitude'],destination['longitude']],
                    mode='lines+markers', name='移植目的地連線（非實際路線）', legendgroup='transplant-destinations', showlegend=not destination_legend_shown, marker={'size':7,'color':'#397c89','opacity':.85},line={'color':'#397c89','width':1.5},
                    customdata=[['official:'+chosen['id'],chosen['location'],chosen['published_at'],chosen['title']],
                                ['official:'+chosen['id'],destination.get('label',''),chosen['published_at'],'移植目的地']],
                    hovertemplate='%{customdata[3]}<br>%{customdata[1]}<extra></extra>'))
                destination_legend_shown = True
    care_items=[]
    for name, rule in CARE_RULES.items():
        matching=int((df['species']==name).sum())
        if matching == 0:
            continue
        months_text = '、'.join(str(m) for m in rule['months'])
        care_items.append(html.Button([html.B(f'{name} · {matching:,} 棵'),html.Small(f"{months_text} 月｜{rule['event']}"),html.P(rule['action'])],id={'type':'care_card','species':name},n_clicks=0,className='care-item card-action',title=f'篩選 {name}'))
    if not care_items:care_items=[html.P('目前篩選無符合的養護觀察樹種。',className='help-text')]
    road_summary=open_df.groupby(['district','road']).agg(cases=('tree_id','size'),high_risk=('risk_level',lambda s:int((s=='高').sum())),avg_age=('case_age_days','mean')).reset_index()
    road_summary=road_summary.sort_values(['high_risk','cases','avg_age'],ascending=False).head(5)
    if road_summary.empty:
        priority=[html.P('目前篩選條件下無待處理示範案件。',className='help-text')]
    else:
        priority=[html.Button([html.B(f"{row.district} · {row.road}"),html.Small(f"待處理 {int(row.cases)} 件｜高風險 {int(row.high_risk)} 棵｜平均 {row.avg_age:.1f} 天")],id={'type':'priority_road','district':row.district,'road':row.road},n_clicks=0,className='priority-item card-action',title=f'篩選 {row.district} {row.road}') for row in road_summary.itertuples()]
    display_columns=['tree_id','district','road','species','issue_type','issue_status','risk_level','risk_score','diameter_cm','height_m','survey_date']
    for col in display_columns:
        if col not in df:df=df.assign(**{col:''})
    rate_value=(len(issue_df)/len(df)*1000) if len(df) else 0
    avg_age=open_df['case_age_days'].mean() if len(open_df) else 0
    return fig,f'{len(df):,}',str(df['district'].nunique()),f'{len(open_df):,}',f'{rate_value:.1f}',f"{int((df['risk_level']=='高').sum()):,}",f'{avg_age:.1f}',str(df['species'].nunique()),f'{count_care:,}',inventory_chart(df,'species',species_colors=SPECIES_COLORS),inventory_chart(df,'district',trees['district'].dropna().unique()),build_issue_chart(df),build_rate_chart(df),df[display_columns].head(1000).fillna('').to_dict('records'),care_items,priority

@callback(Output('priority_chart','figure'),Output('age_chart','figure'),Input('district_filter','value'),Input('road_filter','value'),Input('species_filter','value'),Input('issue_filter','value'),Input('status_filter','value'),Input('month_filter','value'),Input('care_only','value'),Input('drill_filter','data'))
def update_analysis(district,road,species,issue,status,month,care_only,drill):
    df=apply_drill_filter(filter_data(district,road,species,issue,status,month,care_only),drill)
    return priority_chart(df), age_chart(df)

@callback(Output('tree_map','figure',allow_duplicate=True),
          Input('tree_map','relayoutData'),
          State('district_filter','value'),State('road_filter','value'),
          State('species_filter','value'),State('issue_filter','value'),
          State('status_filter','value'),State('month_filter','value'),
          State('care_only','value'),State('map_mode','value'),
          State('drill_filter','data'),State('official_overlay','data'),
          State('official_topic','value'),State('official_query','value'),
          State('official_case_select','value'),prevent_initial_call=True)
def update_map_view(view,*filters):
    if not view or not any(k.startswith('mapbox.') for k in view):
        return no_update
    return update_dashboard(*filters,map_view=view)[0]


@callback(Output('tree_details','children'),Input('tree_map','clickData'))
def show_tree(click_data):
    if not click_data:return '請點選地圖標記，查看該棵樹的資料。'
    custom = click_data.get('points',[{}])[0].get('customdata')
    if not custom:
        return '請放大群聚標記，再點選單一樹木或官方說明。'
    selected_id=str(custom[0])
    if selected_id.startswith('official:'):
        from scripts.official_cases import records_from_geojson, load_snapshot
        record = next((r for r in records_from_geojson(load_snapshot()) if r['id'] == selected_id.split(':',1)[1]), None)
        if not record:
            return '找不到該則官方說明。'
        return html.Div([html.Strong('新增補充資料 · 官方公開說明'), html.H3(record['title']),
                         html.P(record['published_at']+'｜'+record['location']), html.P(record['content'][:350]+'…'),
                         html.A('閱讀官方原文 ↗', href=record['url'], target='_blank', rel='noopener noreferrer',className='source-link'),
                         html.P('這是公開說明，不代表本棵行道樹的移除或移植狀態。',className='help-text')])
    found=trees[trees['tree_id'].astype(str)==selected_id]
    if found.empty:return '找不到所選樹木資料。'
    row=found.iloc[0]
    def text_value(key,suffix=''):
        v=row.get(key,'')
        return '未記載' if pd.isna(v) or str(v)=='' else f'{v}{suffix}'
    fields=[('樹木編號',text_value('tree_id')),('行政區',text_value('district')),('路段',text_value('road')),('樹種',text_value('species')),('通報類型',text_value('issue_type')),('案件狀態',text_value('issue_status')),('風險分級',f"{text_value('risk_level')}（{text_value('risk_score')}）"),('胸徑',text_value('diameter_cm',' cm')),('樹高',text_value('height_m',' m')),('調查日期',text_value('survey_date'))]
    return html.Div([html.Div([html.Span(label),html.Strong(value)],className='detail-row') for label,value in fields])

@callback(Output('csv_download','data'),Input('export_button','n_clicks'),Input('district_filter','value'),Input('road_filter','value'),Input('species_filter','value'),Input('issue_filter','value'),Input('status_filter','value'),Input('month_filter','value'),Input('care_only','value'),Input('drill_filter','data'),prevent_initial_call=True)
def export_data(n_clicks,district,road,species,issue,status,month,care_only,drill_filter):
    if ctx.triggered_id!='export_button':return None
    df=apply_drill_filter(filter_data(district,road,species,issue,status,month,care_only),drill_filter)
    return dcc.send_data_frame(df.to_csv,'taipei_tree_filtered.csv',index=False,encoding='utf-8-sig')


app.clientside_callback(
    """function(a,b) { if(a || b) { document.getElementById('operations').scrollIntoView({behavior:'smooth'}); } return window.dash_clientside.no_update; }""",
    Output('operations','title'), Input('open_cases_link','n_clicks'), Input('high_risk_count_link','n_clicks'), prevent_initial_call=True)

@callback(Output('operations_tab','value'), Input('open_cases_link','n_clicks'), Input('high_risk_count_link','n_clicks'), prevent_initial_call=True)
def open_operations(cases, risk):
    return 'risk' if ctx.triggered_id == 'high_risk_count_link' else 'cases'


def operation_rows(tab, district, road, species, issue, status, month, care_only, drill):
    filtered = apply_drill_filter(filter_data(district,road,species,issue,status,month,care_only),drill)
    df = operations[operations['tree_id'].isin(filtered['tree_id'])]
    if tab == 'cases':
        df = df[df['issue_status'].isin(['待巡檢','處理中'])]
    elif tab == 'risk':
        df = df[df['risk_level'] == '高']
    elif tab == 'work':
        df = df[df['issue_type'] != '無通報']
    return df


operation_inputs = [Input('operations_tab','value'), Input('district_filter','value'),Input('road_filter','value'),Input('species_filter','value'),Input('issue_filter','value'),Input('status_filter','value'),Input('month_filter','value'),Input('care_only','value'),Input('drill_filter','data')]

@callback(Output('operations_table','data'),Output('operations_table','columns'),Output('operations_summary','children'),Output('history_chart','figure'),Output('history_chart','style'),Output('operations_table','active_cell'), *operation_inputs)
def render_operations(tab, *filters):
    df = operation_rows(tab, *filters)
    cols = [('tree_id','樹木編號'),('district','行政區'),('road','路段'),('species','樹種')]
    if tab == 'risk':
        cols += [('risk_score','模擬風險分數'),('risk_level','模擬風險'),('inspection_date','模擬巡檢日'),('team','模擬班組')]
    else:
        cols += [('case_id','模擬案件編號'),('issue_type','通報類型'),('issue_status','模擬狀態'),('reported_date','模擬通報日'),('closed_date','模擬結案日'),('work_action','模擬養護項目'),('team','模擬班組')]
    chart = go.Figure()
    if tab == 'history':
        trend = monthly_trend(df)
        chart.add_bar(x=trend['month'], y=trend['reported'], name='模擬新增通報', marker_color='#b77948')
        chart.add_bar(x=trend['month'], y=trend['closed'], name='模擬結案', marker_color='#426448')
        chart.update_layout(barmode='group',height=320,margin={'l':45,'r':20,'t':25,'b':40},yaxis_title='件數',paper_bgcolor='#fffdf7',plot_bgcolor='#fffdf7',font=dict(color='#2e4534',family='Noto Sans TC, sans-serif'),xaxis=dict(gridcolor='#e9e8dc'),yaxis=dict(gridcolor='#e9e8dc'))
        df = df[df['issue_type'] != '無通報']
    return df[[c for c,_ in cols]].head(1000).fillna('').to_dict('records'), [{'id':c,'name':n} for c,n in cols], f'模擬資料｜符合 {len(df):,} 筆｜點選列可查看履歷', chart, {'display':'block' if tab == 'history' else 'none'}, None

@callback(Output('tree_history','children'), Input('operations_table','active_cell'), Input('operations_table','derived_virtual_data'), Input('operations_tab','value'))
def show_operation_history(cell, rows, tab):
    if not cell or not rows:
        return '請點選上方清單查看模擬巡檢與養護履歷。'
    index = cell.get('row', 0)
    if index >= len(rows):
        return '請重新選取清單資料。'
    tree_id = rows[index]['tree_id']
    row = operations[operations['tree_id'] == tree_id].iloc[0]
    return [html.H4(f'樹木 {tree_id}｜模擬履歷'), *[html.Div([html.Strong(date), html.Span('　'+event)], className='detail-row') for date,event in tree_events(row)]]

@callback(Output('operations_download','data'),Input('operations_export','n_clicks'), *operation_inputs, prevent_initial_call=True)
def export_operations(clicks, tab, *filters):
    if ctx.triggered_id != 'operations_export':
        return no_update
    df = operation_rows(tab, *filters)
    if tab == 'history':
        df = monthly_trend(df)
    return dcc.send_data_frame(df.to_csv, f'simulated_{tab}.csv', index=False, encoding='utf-8-sig')




@callback(Output('map_info_tab','value'),Output('official_case_select','value',allow_duplicate=True),
          Input('tree_map','clickData'),Input('official_cases_table','active_cell'),Input('map_mode','value'),prevent_initial_call=True)
def select_map_record(click_data, table_cell=None, map_mode=None):
    if ctx.triggered_id == 'map_mode':
        return ('official' if map_mode == 'official' else 'tree'),no_update
    if ctx.triggered_id == 'official_cases_table':
        return 'official',no_update
    custom = (click_data or {}).get('points',[{}])[0].get('customdata')
    if not custom:
        return no_update,no_update
    key = str(custom[0])
    return ('official',key.split(':',1)[1]) if key.startswith('official:') else ('tree',no_update)


@callback(Output('tree_info_panel','style'),Output('official_info_panel','style'),Input('map_info_tab','value'))
def switch_map_reader(tab):
    return ({'display':'none'},{'display':'block'}) if tab=='official' else ({'display':'block'},{'display':'none'})


if __name__=='__main__':
    # debug=True 會在每次存檔時重載並把整份 Supabase 資料重讀一遍；開發時再手動開。
    app.run(debug=False,host='127.0.0.1',port=8050)
