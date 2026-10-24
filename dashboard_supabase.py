"""臺北市智慧行道樹管理 Dashboard | Dash MVP"""
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

import json
import colorsys
import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Dash, dcc, html, Input, Output, State, callback, dash_table, ctx, no_update
from scripts.care_calendar import load_care_calendar, month_rules, eligible_species, species_key
from scripts.dashboard_charts import species_donut, district_distribution
from scripts.dashboard_analysis import issue_chart as build_issue_chart, rate_chart as build_rate_chart, priority_chart, age_chart, report_history, history_chart, empty_chart
from scripts.map_points import select_map_points
from scripts.dashboard_work_orders import load_work_orders
from scripts.official_supabase_ui import official_section, official_reader, register_official
from scripts.official_supabase import load_snapshot, records_from_geojson, filter_records
from scripts.supabase_data import load_trees, empty_trees, load_reports, normalize_reports, attach_report_summary, load_demo_reports, normalize_demo_reports, attach_demo_risks
from scripts.civic_data import load_civic, empty_civic, attach_civic, civic_group, ensure_civic_columns

try:
    trees = load_trees()
    data_mode = 'Supabase 資料' if len(trees) else 'Supabase：無可讀取資料（請確認資料表及 RLS 權限）'
except ValueError as error:
    trees = empty_trees()
    data_mode = f'Supabase 尚未載入：{error}'

# 樹靈記 App 的公民護樹資料（認領、樹況、集氣、時間軸、請願）。各表獨立載入，缺哪張就標哪張。
CIVIC = empty_civic()
civic_message = '樹木資料尚未載入，略過公民護樹資料'
if len(trees):
    CIVIC = load_civic()
    civic_message = '；'.join(CIVIC['messages'].values())
trees = attach_civic(trees, CIVIC)
CIVIC_READY = any(len(CIVIC[key]) for key in ('claims', 'status', 'events', 'petitions'))

reports = normalize_reports([])
reports_ready = False
report_message = '樹木資料尚未載入'
if len(trees):
    try:
        reports = load_reports()
        trees = attach_report_summary(trees, reports)
        reports_ready = True
        report_message = f'通報已連線：全庫 {len(reports):,} 筆；圖表只計算目前篩選的樹木範圍（含已排除通報）'
    except ValueError as error:
        report_message = f'通報載入失敗：{error}'

demo_reports = normalize_demo_reports([])
demo_trees = empty_trees()
demo_ready = False
demo_message = '模擬通報尚未載入'
if len(trees):
    try:
        demo_reports = load_demo_reports()
        demo_trees = attach_demo_risks(attach_report_summary(trees, demo_reports), demo_reports)
        demo_ready = True
        demo_message = f'【模擬模式】通報與風險均為虛構展示資料，共 {len(demo_reports):,} 筆；樹籍來自真實行道樹。不可作為實際養護指令。'
    except ValueError as error:
        demo_message = f'模擬資料尚不可用：{error}'
INITIAL_MODE = 'demo' if demo_ready and len(demo_reports) else 'real'


def mode_data(data_source='real'):
    if data_source == 'demo':
        return demo_trees, demo_reports, demo_ready, demo_message
    return trees, reports, reports_ready, report_message


CARE_CALENDAR = {}
care_load_message = '養護月份資料尚未載入'
if len(trees):
    try:
        CARE_CALENDAR = load_care_calendar()
        care_load_message = '養護資料已連線；月份供觀察與專業評估參考'
    except ValueError:
        care_load_message = '養護月份資料尚未載入，請由資料管理者確認匯入與讀取權限。'
CARE_READY = bool(CARE_CALENDAR)
ISSUE_TYPES = sorted(reports['issue_type'].unique())
ISSUE_STATUS = ['待確認','已確認','已排除']
MONTH_OPTIONS = [{'label':f'{i} 月', 'value':i} for i in range(1,13)]
current_month = pd.Timestamp.now(tz='Asia/Taipei').month
SPECIES_PALETTE = ['#355b3e','#9a7952','#85985d','#b77958','#778c9b','#998394','#70948a','#b49b57','#a86f73','#687e76']
OTHER_SPECIES_COLOR = '#8a948e'

external_stylesheets = ['https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;600;700&family=Noto+Serif+TC:wght@500;600&display=swap']
app = Dash(__name__, assets_folder='supabase_assets', title='做伙顧樹 KooTree｜城市樹木資訊與公民照護平台', external_stylesheets=external_stylesheets)
server = app.server
register_official(app)
MAP_STYLE = json.loads((Path(__file__).resolve().parent / "data/map-style.json").read_text())
SPECIES_COLORS = {}
for index, name in enumerate(trees["species"].value_counts().index):
    rgb = colorsys.hsv_to_rgb((index * .61803398875) % 1, .32, .60)
    SPECIES_COLORS[name] = SPECIES_PALETTE[index] if index < len(SPECIES_PALETTE) else "#%02x%02x%02x" % tuple(round(c*255) for c in rgb)

def dropdown(id, label, options, value='all'):
    heading=[html.Span(label),html.Details([html.Summary('i',className='info-button',**{'aria-label':'養護觀察月份說明'}),html.Div([html.Strong('養護觀察月份如何使用'),html.Span('月份會更新當月物候關注數字；勾選月份篩選才會縮小地圖與表格範圍。右側養護卡片只列出所選月份的專屬參考。'),html.Span('專屬月份取自 Supabase 已啟用、附來源的規則；只有全年通用資料的樹種不推估月份。修剪仍須現地專業評估。')],className='info-tooltip')],className='field-info')] if id=='month_filter' else label
    return html.Div([html.Div(heading, className='field-label'), dcc.Dropdown(id=id,options=options,value=value,clearable=False)],className='field')

def opts(series):
    return [{'label':'全部','value':'all'}]+[{'label':str(x),'value':str(x)} for x in sorted(series.dropna().astype(str).unique())]

def metric(title, value_id, icon, tone=''):
    icon_name = {'total_trees':'tree', 'open_cases':'report', 'high_risk_count':'risk', 'case_rate':'rate', 'district_count':'place', 'avg_case_age':'time', 'species_count':'leaf', 'care_count':'season'}[value_id]
    children = [html.Div(html.Span(className='botanical-icon', style={'maskImage':f'url({app.get_asset_url("branding/icons/" + icon_name + ".svg")})'}, **{'aria-hidden':'true'}),className='metric-icon'),html.Div(title,className='metric-label'),html.Div(id=value_id,className='metric-value')]
    if value_id in ['open_cases', 'high_risk_count']:
        children.append(html.Small('點選查看明細 ↗'))
        return html.Button(children, id=f'{value_id}_link', n_clicks=0, className=f'metric metric-action {tone}')
    return html.Div(children,className=f'metric {tone}'.strip())

def civic_tile(title, value_id, icon_name, tone=''):
    return html.Div([html.Div(html.Span(className='botanical-icon', style={'maskImage':f'url({app.get_asset_url("branding/icons/" + icon_name + ".svg")})'}, **{'aria-hidden':'true'}),className='metric-icon'),html.Div(title,className='metric-label'),html.Div(id=value_id,className='metric-value')],className=f'metric {tone}'.strip())

app.layout = html.Div([
    dcc.Store(id='drill_filter', data={}),
    dcc.Store(id='official_overlay', data=[]),
    html.Header([
        html.Div([
            html.Img(src=app.get_asset_url('branding/kootree-logo.png'), alt='KooTree 樹冠與家園標誌', width=80, height=72),
            html.Div([html.H1('做伙顧樹'), html.Div('KooTree', className='c-brand-english'), html.P('做伙顧樹，讓樹安好。')]),
        ], className='c-brand'),
        html.Div(className='c-scene', **{'aria-hidden':'true'}),
        html.Div([html.Span('●',className='online'),html.Span(data_mode,id='source_badge')],className='data-badge')
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
                dropdown('issue_filter','通報類型',opts(mode_data(INITIAL_MODE)[1]['issue_type'])),
                dropdown('status_filter','案件狀態',opts(mode_data(INITIAL_MODE)[1]['issue_status'])),
                dropdown('month_filter','養護觀察月份', MONTH_OPTIONS,current_month),
                dcc.Checklist(id='care_only',options=[{'label':'只看該月份建議關注樹種','value':'care'}],value=[],className='check-option'),
            ],className='filter-block'),
            html.Details([html.Summary('資料模式'),dcc.RadioItems(id='data_source',options=[{'label':'真實通報','value':'real'},{'label':'模擬通報（展示用）','value':'demo'}],value=INITIAL_MODE,className='radio-items')],className='filter-block'),
            html.Div([
                html.Div('資料說明',className='filter-group-title'),
                html.P('樹籍（行道樹與受保護樹木）、通報、認領、樹況、集氣與養護規則來自 Supabase。模擬通報明確標示；缺少的工單、班組及結案日期不推估。',className='help-text'),
                html.A('官方資料集 ↗',href='https://data.taipei/dataset/detail?id=7a49d00c-a5ff-4a6b-be9e-aaa6dc1ff7e8',target='_blank',className='source-link'),
            ],className='filter-block subtle-block'),
        ],className='sidebar'),
        html.Main([
            html.Nav([html.A('樹木總覽',href='#tree-overview'),html.A('樹木與移除／移植地圖',href='#map-workspace'),html.A('公民護樹',href='#civic')],className='data-navigation', **{'aria-label':'資料區塊導覽'}),
            html.Div([
                metric('符合條件樹木','total_trees','樹','primary'),
                metric('待確認通報','open_cases','件','warning'),
                metric('高風險樹木','high_risk_count','警','danger'),
                metric('每千棵通報','case_rate','率','primary'),
                metric('涵蓋行政區','district_count','區'),
                metric('待確認通報平均天數','avg_case_age','天'),
                metric('樹種數量','species_count','種'),
                metric('當月物候關注','care_count','期')
            ],className='metric-grid metric-grid-wide',id='tree-overview'),
            html.Div([
                html.Section([html.Div([html.Div('樹木分布地圖',className='panel-title'),html.Span('點選樹木，認識身邊的老朋友',className='note')],className='section-heading'),html.Div([
                    dcc.RadioItems(id='map_mode', options=[{'label':'樹種分布','value':'species'},{'label':'養護月份提示','value':'care'},{'label':'通報熱點','value':'issue'},{'label':'風險分級','value':'risk'},{'label':'護樹狀態','value':'civic'},{'label':'移除／移植','value':'official'}],value='species',className='map-layer-options',style={'display':'flex','flexWrap':'wrap','gap':'4px'},labelStyle={'display':'inline-flex','alignItems':'center','margin':0},inputStyle={'position':'absolute','opacity':0,'width':'1px','height':'1px'}),
                    html.Button('清除圖卡篩選',id='clear_drill',className='clear-drill-btn map-clear-btn',style={'width':'auto','margin':0}),
                ],className='map-toolbar',style={'display':'flex','alignItems':'center','flexWrap':'wrap','justifyContent':'space-between','gap':'10px'}),html.Div(id='drill_label',className='drill-label muted'),dcc.Graph(id='tree_map',config={'displayModeBar':'hover','displaylogo':False,'scrollZoom':True},style={'height':'540px'}),official_section()],className='surface map-panel'),
                html.Section([
                    dcc.Tabs(id='map_info_tab',value='tree',children=[dcc.Tab(label='樹木資訊',value='tree'),dcc.Tab(label='移除／移植紀錄',value='official')]),
                    html.Div([html.Div('樹木資訊',className='panel-title section-gap'),html.Div('請點選地圖標記，查看該棵樹的資料。',id='tree_details',className='tree-detail'),html.Div('當月養護觀察',className='panel-title section-gap'),html.Div(id='care_list'),html.Div('優先巡檢路段',className='panel-title section-gap'),html.Div(id='priority_roads')],id='tree_info_panel'),
                    html.Div(official_reader(),id='official_info_panel',style={'display':'none'}),
                ],className='surface details-panel')
            ],className='main-grid',id='map-workspace'),
            html.Div([
                html.Section([html.Div('樹種占比 · TOP 10 與其他',className='panel-title'),dcc.Graph(id='species_chart',config={'displayModeBar':False})],className='surface'),
                html.Section([html.Div('行政區樹木數量 · 完整 12 區',className='panel-title'),dcc.Graph(id='district_chart',config={'displayModeBar':False})],className='surface'),
                html.Section([html.Div('通報類型與確認進度',className='panel-title'),html.Details([html.Summary('資料來源與統計方式'),html.P('一筆通報計為一件；行政區通報率以位置與樹種範圍內的完整樹籍為分母。模擬與真實通報分開計算。確認狀態不等同處理或結案狀態。',className='help-text')],className='chart-method'),dcc.Graph(id='issue_chart',config={'displayModeBar':False})],className='surface'),
                html.Section([html.Div('行政區通報率與整體比較',className='panel-title'),dcc.Graph(id='rate_chart',config={'displayModeBar':False})],className='surface'),
                html.Section([html.Div('路段巡檢優先順序',className='panel-title'),html.P('越往右、上代表待確認通報與已評估高風險樹木越多；圓點大小代表路段樹木數。再按同一路段可取消篩選。',className='help-text'),dcc.Graph(id='priority_chart',config={'displayModeBar':False})],className='surface'),
                html.Section([html.Div('待確認通報經過天數',className='panel-title'),html.P('天數依 Supabase 通報日期計算；確認通報不代表已結案，分組不代表法定處理期限。',className='help-text'),dcc.Graph(id='age_chart',config={'displayModeBar':False})],className='surface')
            ],className='chart-grid'),
            html.Section([html.Div([html.Div('篩選結果明細',className='panel-title'),html.Button('匯出篩選 CSV',id='export_button',className='export-btn')],className='section-heading'),
                dash_table.DataTable(id='tree_table',columns=[{'name':label,'id':col} for col,label in [('tree_code','樹木編號'),('tree_kind','類型'),('data_origin','資料性質'),('district','行政區'),('road','路段'),('species','樹種'),('tree_status','樹況'),('watch_count','集氣'),('issue_type','通報類型'),('issue_status','通報確認狀態'),('risk_level','風險'),('risk_score','分數'),('diameter','胸徑（來源值）'),('height','樹高（來源值）'),('survey_date','調查日期')]],page_size=12,sort_action='native',style_table={'overflowX':'auto'},style_cell={'fontFamily':'Noto Sans TC, sans-serif','textAlign':'left','padding':'11px','border':'none','fontSize':'12px','backgroundColor':'#fffdf7','color':'#2e4534'},style_header={'backgroundColor':'#eeeadd','fontWeight':'bold','color':'#455b52'},style_data_conditional=[{'if':{'row_index':'odd'},'backgroundColor':'#faf8f1'}]),dcc.Download(id='csv_download')],className='surface table-panel'),
            html.Section([
                html.Div('案件與養護資料中心', className='panel-title'),html.P(id='source_message',className='help-text'),
                html.P('與上方篩選同步，逐筆呈現 Supabase 通報與已提供的風險評估。工單、班組、巡檢與結案資料若尚未提供，會明確提示。', className='help-text'),
                dcc.Tabs(id='operations_tab', value='cases', children=[
                    dcc.Tab(label='待確認通報', value='cases'),
                    dcc.Tab(label='高風險樹木', value='risk'),
                    dcc.Tab(label='養護工單', value='work'),
                    dcc.Tab(label='歷史趨勢', value='history'),
                ]),
                html.Div(id='operations_summary', className='drill-label active'),
                dcc.Graph(id='history_chart', config={'displayModeBar':False}),
                dash_table.DataTable(id='operations_table', page_size=10, sort_action='native', filter_action='native',
                    style_table={'overflowX':'auto'}, style_cell={'textAlign':'left','padding':'12px','fontFamily':'inherit','minWidth':'100px','backgroundColor':'#fffdf7','color':'#2e4534'},
                    style_header={'backgroundColor':'#eef0e3','fontWeight':'bold'}),
                html.P('點選清單任一列，查看資料庫記載的通報履歷。清單最多顯示 1,000 筆；匯出依目前分頁排序及篩選。', className='help-text'),
                html.Button('匯出目前分頁 CSV', id='operations_export', className='export-btn'),
                dcc.Download(id='operations_download'),
                html.Div(id='tree_history', className='tree-detail'),
            ], className='surface table-panel', id='operations'),
            html.Section([
                html.Div([html.Div('公民護樹 · 樹靈記 App 即時資料', className='panel-title'),html.Span('認領、集氣、樹況與請願由 App 使用者產生，與通報同為真實資料',className='note')],className='section-heading'),
                html.P(civic_message, className='help-text'),
                html.Div([
                    civic_tile('認領中樹木','civic_claims','leaf','primary'),
                    civic_tile('集氣票數','civic_watch','rate'),
                    civic_tile('樹況非健康','civic_status','risk','warning'),
                    civic_tile('請願空位 · 募集中','civic_petitions','place'),
                ],className='metric-grid'),
                dcc.Tabs(id='civic_tab', value='status', children=[
                    dcc.Tab(label='樹況與集氣', value='status'),
                    dcc.Tab(label='認領中', value='claims'),
                    dcc.Tab(label='時間軸', value='events'),
                    dcc.Tab(label='請願進度', value='petitions'),
                ]),
                html.Div(id='civic_summary', className='drill-label active'),
                dash_table.DataTable(id='civic_table', page_size=10, sort_action='native', filter_action='native',
                    style_table={'overflowX':'auto'}, style_cell={'textAlign':'left','padding':'12px','fontFamily':'inherit','minWidth':'100px','backgroundColor':'#fffdf7','color':'#2e4534'},
                    style_header={'backgroundColor':'#eef0e3','fontWeight':'bold'}),
                html.P('樹況只有已驗證的管理單位帳號能改；集氣達 30 票、單位 7 天未回應等規則在 App 端。請願空位依地址比對行政區。清單最多顯示 1,000 筆。', className='help-text'),
            ], className='surface table-panel', id='civic'),
            html.Footer([html.Div('做伙顧樹 KooTree · 做伙顧樹，讓樹安好。',className='footer-brand'),html.Span('資料來源：Supabase（臺北市文化局受保護樹木、公園處行道樹、樹靈記 App 公民資料）｜模擬通報、風險分數均有標示，不可作為實際樹木安全診斷或養護指示。')],className='footer')
        ],className='content')
    ],className='layout')
],className='page')

@app.callback(Output('source_badge','children'),Output('source_message','children'),
          Output('issue_filter','options'),Output('issue_filter','value'),
          Output('status_filter','options'),Output('status_filter','value'),
          Input('data_source','value'))
def update_source(data_source):
    selected_trees, selected_reports, ready, message = mode_data(data_source)
    if data_source == 'demo' and ready:
        evaluated = selected_trees['risk_score'].notna().sum()
        message += f' 模擬評估涵蓋 {evaluated:,} 棵，其餘樹木沒有風險評估。'
    badge = 'Supabase · 模擬資料（展示用）' if data_source == 'demo' else data_mode
    return badge, message, opts(selected_reports['issue_type']), 'all', opts(selected_reports['issue_status']), 'all'


@app.callback(Output('road_filter','options'),Output('road_filter','value'),Input('district_filter','value'))
def update_roads(district):
    df=trees if district=='all' else trees[trees['district']==district]
    return opts(df['road']), 'all'

def update_drill_filter(care_clicks,road_clicks,issue_click,clear_clicks,district,road,species,issue,status,month,care_only,map_mode,data_source,current_filter=None):
    trigger = ctx.triggered_id
    if trigger == 'clear_drill':
        return {}
    if isinstance(trigger, str) and trigger in {'district_filter','road_filter','species_filter','issue_filter','status_filter','month_filter','care_only','map_mode','data_source'}:
        return {}
    if isinstance(trigger, dict):
        # Dashboard redraws recreate cards at n_clicks=0. These events must not
        # toggle the selection or depend on click-count parity.
        inputs = [item for group in ctx.inputs_list[:2] for item in group]
        if not any(item.get('id') == trigger and item.get('value') for item in inputs):
            return no_update
    current_filter = current_filter or {}
    if isinstance(trigger, dict) and trigger.get('type') == 'care_card':
        species = trigger.get('species')
        if current_filter.get('type') == 'species' and current_filter.get('species') == species:
            return {}
        return {'type':'species','species':species,'label':f'物候關注樹種：{species}'}
    if isinstance(trigger, dict) and trigger.get('type') == 'priority_road':
        district = trigger.get('district')
        road = trigger.get('road')
        if current_filter.get('type') == 'road' and current_filter.get('district') == district and current_filter.get('road') == road:
            return {}
        return {'type':'road','district':district,'road':road,'label':f'優先巡檢路段：{district} · {road}'}
    if trigger == 'issue_chart' and issue_click:
        issue_type = issue_click['points'][0].get('y')
        if issue_type:
            return {'type':'issue','issue_type':issue_type,'label':f'通報類型：{issue_type}'}
    return no_update

@app.callback(Output('drill_label','children'),Output('drill_label','className'),Input('drill_filter','data'))
def render_drill_label(drill_filter):
    if not drill_filter:
        return '點選地圖查看樹木；點選圖卡或通報圖表可篩選。','drill-label muted'
    hint = '（再點同張卡片取消）' if drill_filter.get('type') in {'species','road'} else ''
    return f"目前鑽取：{drill_filter.get('label','自訂條件')}{hint}",'drill-label active'

def filter_data(district,road,species,issue,status,month,care_only,data_source='real'):
    selected_trees, selected_reports, _, _ = mode_data(data_source)
    df=selected_trees
    for col,value in [('district',district),('road',road),('species',species)]:
        if value != 'all':df=df[df[col]==value]
    if issue != 'all' or status != 'all':
        matching = filter_reports(selected_reports, issue, status)
        df = df[df['tree_id'].isin(matching['tree_id'])]
    if 'care' in (care_only or []):
        allowed=eligible_species(CARE_CALENDAR, df['species'], month)
        df=df[df['species'].isin(allowed)]
    return df

def apply_drill_filter(df, drill_filter, data_source='real'):
    if not drill_filter:
        return df
    kind = drill_filter.get('type')
    if kind == 'district':
        return df[df['district'].eq(drill_filter.get('district'))]
    if kind == 'species_set':
        return df[df['species'].isin(drill_filter.get('species',[]))]
    if kind == 'species':
        return df[df['species'] == drill_filter.get('species')]
    if kind == 'road':
        return df[(df['district'] == drill_filter.get('district')) & (df['road'] == drill_filter.get('road'))]
    if kind == 'issue':
        selected_reports = mode_data(data_source)[1]
        matching = selected_reports[selected_reports['issue_type'].eq(drill_filter.get('issue_type'))]
        return df[df['tree_id'].isin(matching['tree_id'])]
    return df

def filter_reports(source, issue='all', status='all', drill_filter=None):
    selected = source
    if issue != 'all':
        selected = selected[selected['issue_type'].eq(issue)]
    if status != 'all':
        selected = selected[selected['issue_status'].eq(status)]
    if drill_filter and drill_filter.get('type') == 'issue':
        selected = selected[selected['issue_type'].eq(drill_filter.get('issue_type'))]
    return selected


def report_empty_figure(message, title):
    chart = go.Figure()
    chart.update_layout(height=310, margin={'l':25,'r':25,'t':20,'b':35},
                        paper_bgcolor='white', plot_bgcolor='white',
                        xaxis={'visible':False}, yaxis={'visible':False})
    chart.add_annotation(text=message, x=.5, y=.5, xref='paper', yref='paper', showarrow=False)
    return chart


def render_care_items(df, month):
    if not CARE_READY:
        return [html.P(care_load_message, className='help-text')]
    if df.empty:
        return [html.P('目前篩選範圍沒有樹木。', className='help-text')]
    names = df['species'].value_counts()
    result = [html.P('花果期及歷史月曆僅供參考，修剪須現地專業評估。', className='help-text')]
    cards, uncatalogued, conflicts = [], 0, 0
    for name, count in names.items():
        profile = CARE_CALENDAR.get(species_key(name))
        if not profile:
            uncatalogued += 1
            continue
        rules = month_rules(CARE_CALENDAR, name, month)
        conflicts += any(row['evidence_level'] == 'conflict' for row in profile['rules'])
        if not rules:
            continue
        events = f'{month} 月｜' + ' · '.join(dict.fromkeys(row['event'] for row in rules))
        actions = ' '.join(dict.fromkeys(row['action'] for row in rules))
        sources = list(dict.fromkeys(url for row in rules for url in row['source_urls']))
        cards.append(html.Div([
            html.Button([html.B(f'{name} · {count:,} 棵'), html.Small(events), html.P(actions)],
                        id={'type':'care_card','species':name}, n_clicks=0,
                        className='care-item card-action', title=f'篩選 {name}；再點一次取消'),
            html.Details([html.Summary('參考依據／待現地確認'),
                          *[html.P(row['source_note'], className='help-text') for row in rules],
                          *[html.A(f'來源 {index+1} ↗',href=url,target='_blank',rel='noopener noreferrer',className='source-link') for index,url in enumerate(sources)]], className='help-text')
        ]))
    if cards:
        result.extend(cards[:6])
        if len(cards) > 6:
            result.append(html.Details([html.Summary(f'其他 {len(cards)-6} 種本月參考'), *cards[6:]]))
    else:
        result.append(html.P(f'{month} 月目前沒有符合篩選條件的樹種專屬月份參考；持續例行觀察。',className='help-text'))
    if conflicts:
        result.append(html.P(f'{conflicts} 個樹種存在月份來源差異；衝突規則未啟用。',className='help-text'))
    if uncatalogued:
        result.append(html.P(f'{uncatalogued} 個樹種名稱尚未建檔，請資料管理者補登。',className='help-text'))
    return result


@app.callback(Output('tree_map','figure'),Output('total_trees','children'),Output('district_count','children'),Output('open_cases','children'),Output('case_rate','children'),Output('high_risk_count','children'),Output('avg_case_age','children'),Output('species_count','children'),Output('care_count','children'),Output('species_chart','figure'),Output('district_chart','figure'),Output('issue_chart','figure'),Output('rate_chart','figure'),Output('tree_table','data'),Output('care_list','children'),Output('priority_roads','children'),Input('district_filter','value'),Input('road_filter','value'),Input('species_filter','value'),Input('issue_filter','value'),Input('status_filter','value'),Input('month_filter','value'),Input('care_only','value'),Input('map_mode','value'),Input('drill_filter','data'),Input('data_source','value'),Input('official_topic','value'),Input('official_query','value'),Input('official_case_select','value'))
def update_dashboard(district,road,species,issue,status,month,care_only,map_mode,drill_filter,data_source='real',official_topic='all',official_query='',official_selected=None,map_view=None):
    _, reports, reports_ready, _ = mode_data(data_source)
    df=ensure_civic_columns(apply_drill_filter(filter_data(district,road,species,issue,status,month,care_only,data_source),drill_filter,data_source))
    # The denominator is the location/species population before report filters.
    population = filter_data(district,road,species,'all','all',month,care_only,data_source)
    spatial_drill = drill_filter if (drill_filter or {}).get('type') != 'issue' else None
    population = apply_drill_filter(population, spatial_drill, data_source)
    issue_df = filter_reports(reports, issue, status, drill_filter)
    issue_df = issue_df[issue_df['tree_id'].isin(df['tree_id'])]
    if issue != 'all' or status != 'all' or (drill_filter or {}).get('type') == 'issue':
        df = df[df['tree_id'].isin(issue_df['tree_id'])]
    open_df = issue_df[issue_df['issue_status'].eq('待確認')]
    open_df = open_df.merge(df[['tree_id','district','road','risk_level']], on='tree_id', how='left')
    missing_report_text = '通報尚未連線，請查看載入狀態'
    if reports_ready:
        missing_report_text = f'目前篩選範圍無通報（全庫 {len(reports):,} 筆）'
    care_species = set(eligible_species(CARE_CALENDAR, df['species'], month))
    count_care = int(df['species'].isin(care_species).sum())
    top_species=df['species'].value_counts().head(10).index.tolist()
    color_map={name:SPECIES_COLORS.get(name,OTHER_SPECIES_COLOR) for name in top_species}
    fig=go.Figure()
    map_df = df[df['tree_id'].isin(issue_df['tree_id'])] if map_mode == 'issue' else df
    plotted,_=select_map_points(map_df,map_view)
    fig.add_annotation(text=f'目前範圍完整顯示 {len(plotted):,} 棵｜每個點位代表一棵樹',x=0,y=1,xref='paper',yref='paper',xanchor='left',yanchor='top',showarrow=False,bgcolor='#fcfaf3',borderpad=6,font={'size':12,'color':'#294e36'})
    if plotted.empty:
        fig.add_trace(go.Scattermapbox(lat=[],lon=[],showlegend=False))
        fig.add_annotation(text='目前篩選無資料，請調整條件',x=.5,y=.5,xref='paper',yref='paper',showarrow=False)
    if len(plotted):
        if map_mode=='care':
            plotted=plotted.copy()
            care_labels = {}
            for name in plotted['species'].unique():
                rules=[r for r in CARE_CALENDAR.get(species_key(name),{}).get('rules',[]) if r['enabled'] and r['rule_type']!='general']
                months=sorted({m for rule in rules for m in rule['months']})
                if months:care_labels[name]=f"{name} · {','.join(map(str,months))} 月"
            plotted['map_group']=plotted['species'].map(care_labels).fillna('其他樹木')
            groups=['其他樹木']+list(care_labels.values())
            colors={'其他樹木':'#77867f',**{label:SPECIES_COLORS.get(name,OTHER_SPECIES_COLOR) for name,label in care_labels.items()}}
        elif map_mode=='issue':
            plotted=plotted.copy()
            plotted['map_group']=plotted['issue_type']
            groups=sorted(set(plotted['map_group']))
            colors={'落枝枯枝':'#b9473f','側枝遮擋':'#cf7c2b','根系隆起':'#8c6f34','病蟲害':'#8b579c','落果飛絮':'#d49b3b','樹木傾斜':'#7c3f33','疑似病徵':'#8b579c','斷枝倒伏':'#b9473f','被砍或消失':'#7c3f33','樹牌遺失':'#8c6f34','新植':'#287c5d','其他':'#9a7952','無通報':'#6b8f80'}
        elif map_mode=='risk':
            plotted=plotted.copy()
            plotted['map_group']=plotted['risk_level']
            groups=['高','中','低','未提供']
            colors={'高':'#bd3f37','中':'#d99132','低':'#287c5d','未提供':'#8a948e'}
        elif map_mode=='civic':
            plotted=plotted.copy()
            plotted['map_group']=civic_group(plotted)
            groups=['已移除','預定移除','觀察中','已補植','集氣中','認領中','無紀錄']
            colors={'已移除':'#7c3f33','預定移除':'#bd3f37','觀察中':'#d99132','已補植':'#397c89','集氣中':'#80629b','認領中':'#287c5d','無紀錄':'#b8bfb6'}
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
            custom=part[['tree_id','district','road','species','tree_code','tree_kind','tree_status','watch_count']].fillna('').astype(str).to_numpy()
            fig.add_trace(go.Scattermapbox(lat=part['latitude'],lon=part['longitude'],mode='markers',name=group,
                marker={'size':(5.5 if group in ('其他樹木','無紀錄') else 8) if map_mode in ('care','civic') else (5 if map_mode=='official' else (10 if group=='高' else 7)),'color':colors.get(group,'#587746'),'opacity':(.4 if group in ('其他樹木','無紀錄') else .95) if map_mode in ('care','civic') else (.35 if map_mode=='official' else .88)},customdata=custom,
                hovertemplate='<b>%{customdata[3]}</b>｜%{customdata[5]}<br>%{customdata[1]} · %{customdata[2]}<br>樹木編號: %{customdata[4]}<br>樹況: %{customdata[6]}｜集氣 %{customdata[7]}<extra></extra>'))
    fig.update_layout(mapbox={'style':MAP_STYLE,'center':{'lat':float(df['latitude'].median()) if len(df) else 25.05,'lon':float(df['longitude'].median()) if len(df) else 121.54},'zoom':12 if district=='all' else 13.3},margin={'l':0,'r':0,'t':0,'b':96},height=540,paper_bgcolor='#fffdf7',legend={'title':{'text':'圖例'},'orientation':'h','y':-.025,'yanchor':'top','x':0,'bgcolor':'#f2f3e8','bordercolor':'#e2e6d6','borderwidth':1,'font':{'size':12,'color':'#45603f'},'itemwidth':30,'itemsizing':'constant'},uirevision=f'{district}-{road}',legend_uirevision=f'{map_mode}')
    if map_mode=='official':
        add_official_overlay(fig,official_topic,official_query,official_selected)
    def issue_fig():
        return build_issue_chart(issue_df,data_source=='demo') if reports_ready and not issue_df.empty else report_empty_figure(missing_report_text,'通報件數')
    def rate_fig():
        return build_rate_chart(population,issue_df) if reports_ready else report_empty_figure(missing_report_text,'件／千棵')
    care_items=render_care_items(df, month)
    road_summary=open_df.groupby(['district','road']).agg(cases=('id','size'),avg_age=('case_age_days','mean')).reset_index()
    road_summary=road_summary.sort_values(['cases','avg_age'],ascending=False).head(5)
    if road_summary.empty:
        priority=[html.P('目前範圍沒有待確認通報。' if reports_ready else '通報尚未連線。',className='help-text')]
    else:
        priority=[html.Button([html.B(f"{row.district} · {row.road}"),html.Small(f"待確認 {int(row.cases)} 件")],id={'type':'priority_road','district':row.district,'road':row.road},n_clicks=0,className='priority-item card-action',title=f'篩選 {row.district} {row.road}；再點一次取消') for row in road_summary.itertuples()]
    df = df.copy()
    df['data_origin'] = '模擬通報／風險' if data_source == 'demo' else '真實資料'
    if 'report_count' in df:
        df = df.sort_values('report_count', ascending=False, kind='stable')
    display_columns=['tree_id','tree_code','tree_kind','data_origin','district','road','species','tree_status','watch_count','issue_type','issue_status','risk_level','risk_score','diameter','height','survey_date']
    for col in display_columns:
        if col not in df:df=df.assign(**{col:''})
    rate_value=(len(issue_df)/len(population)*1000) if len(population) else None
    has_risk = len(df) > 0 and (df['risk_level'].ne('未提供').any() if data_source == 'demo' else df['risk_level'].ne('未提供').all())
    has_age = reports_ready and len(open_df) > 0 and open_df['case_age_days'].notna().all()
    avg_age=open_df['case_age_days'].mean() if has_age else None
    return fig,f'{len(df):,}',str(df['district'].nunique()),f'{len(open_df):,}' if reports_ready else '尚未連線',f'{rate_value:.2f}' if reports_ready and rate_value is not None else '無法計算',f"{int((df['risk_level']=='高').sum()):,}" if has_risk else '缺少評估',f'{avg_age:.1f}' if has_age else ('無待確認' if reports_ready and open_df.empty else '缺少日期'),str(df['species'].nunique()),f'{count_care:,}' if CARE_READY else '尚未載入',species_donut(df,SPECIES_COLORS),district_distribution(df),issue_fig(),rate_fig(),df[display_columns].head(1000).astype(object).where(pd.notna(df[display_columns].head(1000)), '未提供').to_dict('records'),care_items,priority

@app.callback(Output('tree_details','children'),Input('tree_map','clickData'),Input('data_source','value'))
def show_tree(click_data,data_source='real'):
    trees = mode_data(data_source)[0]
    if not click_data:return '請點選地圖標記，查看該棵樹的資料。'
    selected_id=str(click_data['points'][0].get('customdata',[''])[0])
    if selected_id.startswith('official:'):return no_update
    found=trees[trees['tree_id'].astype(str)==selected_id]
    if found.empty:return '找不到所選樹木資料。'
    row=found.iloc[0]
    def text_value(key,suffix=''):
        v=row.get(key,'')
        return '未記載' if pd.isna(v) or str(v)=='' else f'{v}{suffix}'
    claim_text='認領中' if bool(row.get('claimed')) else '無人認領'
    fields=[('樹木編號',text_value('tree_code')),('樹木類型',text_value('tree_kind')),('樹況（App）',text_value('tree_status')),('集氣票數',text_value('watch_count')),('樹靈認領',claim_text),('行政區',text_value('district')),('路段',text_value('road')),('樹種',text_value('species')),('通報類型',text_value('issue_type')),('通報確認狀態',text_value('issue_status')),('風險分級',f"{text_value('risk_level')}（{text_value('risk_score')}）"),('胸徑（來源值）',text_value('diameter')),('樹高（來源值）',text_value('height')),('調查日期',text_value('survey_date')),('掛牌日期',text_value('tagged_date')),('資料建立時間',text_value('source_created_at'))]
    return html.Div([html.Div([html.Span(label),html.Strong(value)],className='detail-row') for label,value in fields])

@app.callback(Output('csv_download','data'),Input('export_button','n_clicks'),Input('district_filter','value'),Input('road_filter','value'),Input('species_filter','value'),Input('issue_filter','value'),Input('status_filter','value'),Input('month_filter','value'),Input('care_only','value'),Input('drill_filter','data'),Input('data_source','value'),prevent_initial_call=True)
def export_data(n_clicks,district,road,species,issue,status,month,care_only,drill_filter,data_source='real'):
    reports = mode_data(data_source)[1]
    if ctx.triggered_id!='export_button':return None
    df=apply_drill_filter(filter_data(district,road,species,issue,status,month,care_only,data_source),drill_filter,data_source)
    if issue != 'all' or status != 'all' or (drill_filter or {}).get('type') == 'issue':
        matching = filter_reports(reports, issue, status, drill_filter)
        df = df[df['tree_id'].isin(matching['tree_id'])]
    df = df.copy()
    df['data_origin'] = '模擬通報／風險' if data_source == 'demo' else '真實資料'
    return dcc.send_data_frame(df.to_csv,'kootree_simulated.csv' if data_source == 'demo' else 'taipei_tree_filtered.csv',index=False,encoding='utf-8-sig')



def add_official_overlay(fig, topic='all', query='', selected=None):
    snapshot=load_snapshot()
    rows=[r for r in filter_records(records_from_geojson(snapshot),topic,query) if r['latitude'] is not None and r['longitude'] is not None]
    if snapshot.get('error'):
        fig.add_annotation(text='官方紀錄尚未匯入 Supabase；請展開下方資料說明',xref='paper',yref='paper',x=.5,y=.5,showarrow=False,bgcolor='#fffdf7',borderpad=12)
    categories=[('removal','原文提及移除','#a45547'),('transplant','原文提及移植','#397c89'),('both','原文提及移除及移植','#80629b'),('other','其他官方說明','#8a9480')]
    for category,label,color in categories:
        selected_rows=[]
        for row in rows:
            key='both' if row['mentions_removal'] and row['mentions_transplant'] else 'removal' if row['mentions_removal'] else 'transplant' if row['mentions_transplant'] else 'other'
            if key==category:selected_rows.append(row)
        if selected_rows:
            fig.add_trace(go.Scattermapbox(lat=[r['latitude'] for r in selected_rows],lon=[r['longitude'] for r in selected_rows],mode='markers',name=label,uid='official-'+category,marker={'size':11,'color':color,'opacity':.95},customdata=[['official:'+r['id'],r['location'],r['published_at'],r['title']] for r in selected_rows],hovertemplate='<b>%{customdata[3]}</b><br>%{customdata[1]}<br>%{customdata[2]}｜'+label+'（非執行狀態）<extra></extra>'))
    chosen=next((r for r in rows if r['id']==selected),None)
    if chosen:
        if ctx.triggered_id=='official_case_select':fig.update_layout(mapbox_center={'lat':chosen['latitude'],'lon':chosen['longitude']},mapbox_zoom=14,uirevision='official-'+selected)
        for destination in chosen['destinations']:
            if destination.get('latitude') is None or destination.get('longitude') is None:continue
            fig.add_trace(go.Scattermapbox(lat=[chosen['latitude'],destination['latitude']],lon=[chosen['longitude'],destination['longitude']],mode='lines+markers',name='移植目的地連線（非實際路線）',line={'color':'#397c89','width':1.5},marker={'size':7,'color':'#397c89'},customdata=[['official:'+chosen['id']]]*2,hovertemplate='官方記載的移植目的地<extra></extra>'))


@app.callback(Output('map_info_tab','value'),Output('official_case_select','value',allow_duplicate=True),Input('tree_map','clickData'),Input('official_cases_table','active_cell'),Input('map_mode','value'),prevent_initial_call=True)
def select_map_record(click_data,table_cell=None,map_mode=None):
    if ctx.triggered_id=='map_mode':return ('official' if map_mode=='official' else 'tree'),no_update
    if ctx.triggered_id=='official_cases_table':return 'official',no_update
    custom=(click_data or {}).get('points',[{}])[0].get('customdata')
    if not custom:return no_update,no_update
    key=str(custom[0])
    return ('official',key.split(':',1)[1]) if key.startswith('official:') else ('tree',no_update)


@app.callback(Output('tree_info_panel','style'),Output('official_info_panel','style'),Input('map_info_tab','value'))
def switch_map_reader(tab):
    return ({'display':'none'},{'display':'block'}) if tab=='official' else ({'display':'block'},{'display':'none'})


def selected_report_data(district,road,species,issue,status,month,care_only,drill,data_source):
    selected_trees, selected_reports, ready, _=mode_data(data_source)
    population=apply_drill_filter(filter_data(district,road,species,issue,status,month,care_only,data_source),drill,data_source)
    report_rows=filter_reports(selected_reports,issue,status,drill)
    report_rows=report_rows[report_rows.tree_id.isin(population.tree_id)]
    return population,report_rows,ready


analysis_inputs=[Input('district_filter','value'),Input('road_filter','value'),Input('species_filter','value'),Input('issue_filter','value'),Input('status_filter','value'),Input('month_filter','value'),Input('care_only','value'),Input('drill_filter','data'),Input('data_source','value')]


@app.callback(Output('priority_chart','figure'),Output('age_chart','figure'),*analysis_inputs)
def update_analysis(*filters):
    population,report_rows,ready=selected_report_data(*filters)
    if not ready:return (empty_chart('通報資料尚未連線'),)*2
    return priority_chart(population,report_rows),age_chart(report_rows)


app.clientside_callback("""function(a,b){if(a||b){document.getElementById('operations').scrollIntoView({behavior:'smooth'});}return window.dash_clientside.no_update;}""",Output('operations','title'),Input('open_cases_link','n_clicks'),Input('high_risk_count_link','n_clicks'),prevent_initial_call=True)


@app.callback(Output('operations_tab','value'),Input('open_cases_link','n_clicks'),Input('high_risk_count_link','n_clicks'),prevent_initial_call=True)
def open_operations(cases,risk):
    return 'risk' if ctx.triggered_id=='high_risk_count_link' else 'cases'


def operation_rows(tab,*filters):
    population,report_rows,ready=selected_report_data(*filters)
    if tab=='risk':
        data=population[population.risk_level.eq('高')].copy()
        data['id']=data.tree_id
        return data
    data=report_history(population,report_rows)
    if tab=='cases':data=data[data.issue_status.eq('待確認')]
    if tab=='work':
        jobs,message=load_work_orders()
        selected=jobs[jobs.is_simulated.eq(filters[-1]=='demo')]
        data=selected.merge(population[['tree_id','tree_code','district','road','species']],left_on='tree_location_id',right_on='tree_id',how='inner',suffixes=('_work',''))
        data.attrs['source_message']=message
        return data
    return data


@app.callback(Output('operations_table','data'),Output('operations_table','columns'),Output('operations_summary','children'),Output('history_chart','figure'),Output('history_chart','style'),Output('operations_table','active_cell'),Input('operations_tab','value'),*analysis_inputs)
def render_operations(tab,*filters):
    data=operation_rows(tab,*filters)
    columns=[('tree_code','樹木編號'),('district','行政區'),('road','路段'),('species','樹種')]
    if tab=='risk':columns += [('risk_score','風險分數'),('risk_level','風險分級')]
    elif tab=='work':columns += [('id','工單編號'),('job_status','工單狀態'),('action','養護項目'),('team','承辦班組'),('inspection_at','巡檢日期'),('completed_at','完成日期')]
    else:columns += [('id','通報編號'),('issue_type','通報類型'),('issue_status','確認狀態'),('reported_date','通報日期')]
    simulated=filters[-1]=='demo'
    summary=f"{'模擬資料' if simulated else '真實通報'}｜符合 {len(data):,} 筆｜點選列可查看履歷"
    if tab=='work':summary=data.attrs.get('source_message','')+f'｜符合 {len(data):,} 筆'
    chart=history_chart(data) if tab=='history' else go.Figure()
    frame=data.reindex(columns=list(dict.fromkeys(['id']+[c for c,_ in columns])))
    rows=frame.head(1000).astype(object).where(pd.notna(frame.head(1000)),'未提供').to_dict('records')
    return rows,[{'id':c,'name':n} for c,n in columns],summary,chart,{'display':'block' if tab=='history' else 'none'},None


@app.callback(Output('tree_history','children'),Input('operations_table','active_cell'),Input('operations_table','derived_virtual_data'),Input('data_source','value'))
def show_operation_history(cell,rows,data_source):
    if not cell or not rows:return '請點選上方清單查看 Supabase 已記載的通報履歷。'
    selected=next((r for r in rows if r.get('id')==cell.get('row_id')),None)
    if selected is None:
        index=cell.get('row',0)
        if index>=len(rows):return '請重新選取清單資料。'
        selected=rows[index]
    tree_code=selected.get('tree_code')
    selected_trees,selected_reports,_,_=mode_data(data_source)
    ids=selected_trees.loc[selected_trees.tree_code.eq(tree_code),'tree_id']
    reports_for_tree=selected_reports[selected_reports.tree_id.isin(ids)].sort_values('created_at')
    children=[html.H4(f"樹木 {tree_code}｜{'模擬' if data_source=='demo' else '真實'}通報履歷")]
    for row in reports_for_tree.itertuples():
        children.append(html.Div([html.Strong(str(row.created_at)),html.Span(f'　通報：{row.issue_type}｜確認狀態：{row.issue_status}')],className='detail-row'))
    events=CIVIC['events']
    for event in events[events.tree_id.isin(ids)].sort_values('created_at').itertuples():
        children.append(html.Div([html.Strong(local_time(event.created_at)),html.Span(f'　{event.kind_label}：{event.title}｜{event.actor_label}')],className='detail-row'))
    jobs,_=load_work_orders()
    for job in jobs[jobs.tree_location_id.astype(str).isin(ids) & jobs.is_simulated.eq(data_source=='demo')].itertuples():
        children.append(html.Div([html.Strong(str(job.created_at)),html.Span(f'　工單：{job.action}｜{job.job_status}｜班組：{job.team or "未提供"}')],className='detail-row'))
        if pd.notna(job.completed_at):children.append(html.Div([html.Strong(str(job.completed_at)),html.Span('　工單完成（不代表通報結案）')],className='detail-row'))
    children.append(html.P('通報確認狀態與工單執行狀態分開記錄；已確認不代表已結案。',className='help-text'))
    return children


@app.callback(Output('operations_download','data'),Input('operations_export','n_clicks'),State('operations_table','derived_viewport_data'),State('operations_tab','value'),State('data_source','value'),prevent_initial_call=True)
def export_operations(clicks,rows,tab,data_source):
    if not clicks:return no_update
    return dcc.send_data_frame(pd.DataFrame(rows or []).to_csv,f"kootree_{data_source}_{tab}_page.csv",index=False,encoding='utf-8-sig')


@app.callback(Output('tree_map','figure',allow_duplicate=True),Input('tree_map','relayoutData'),State('district_filter','value'),State('road_filter','value'),State('species_filter','value'),State('issue_filter','value'),State('status_filter','value'),State('month_filter','value'),State('care_only','value'),State('map_mode','value'),State('drill_filter','data'),State('data_source','value'),State('official_topic','value'),State('official_query','value'),State('official_case_select','value'),prevent_initial_call=True)
def update_map_view(view,*filters):
    if not view or not any(k.startswith('mapbox.') for k in view):return no_update
    return update_dashboard(*filters,map_view=view)[0]


@app.callback(Output('drill_filter','data'),Output('species_chart','clickData'),Output('district_chart','clickData'),Output('rate_chart','clickData'),Output('issue_chart','clickData'),Output('priority_chart','clickData'),Input({'type':'care_card','species':ALL},'n_clicks'),Input({'type':'priority_road','district':ALL,'road':ALL},'n_clicks'),Input('issue_chart','clickData'),Input('clear_drill','n_clicks'),Input('district_filter','value'),Input('road_filter','value'),Input('species_filter','value'),Input('issue_filter','value'),Input('status_filter','value'),Input('month_filter','value'),Input('care_only','value'),Input('map_mode','value'),Input('data_source','value'),State('drill_filter','data'),Input('priority_chart','clickData'),Input('species_chart','clickData'),Input('species_chart','relayoutData'),State('species_chart','figure'),Input('district_chart','clickData'),Input('rate_chart','clickData'),prevent_initial_call=True)
def update_reference_drill(*args):
    basics=args[:14]; priority_click,species_click,species_relayout,species_figure,district_click,rate_click=args[14:]
    trigger=ctx.triggered_id;current=basics[-1] or {};candidate=None
    if trigger in ('district_chart','rate_chart'):
        click=district_click if trigger=='district_chart' else rate_click
        if click:candidate={'type':'district','district':click['points'][0]['y'],'label':'行政區：'+click['points'][0]['y']}
    elif trigger=='priority_chart' and priority_click:
        district,road=priority_click['points'][0]['customdata'][:2]
        candidate={'type':'road','district':district,'road':road,'label':f'優先巡檢路段：{district} · {road}'}
    elif trigger=='species_chart':
        labels=(species_figure or {}).get('data',[{}])[0].get('labels',[])
        if ctx.triggered[0]['prop_id'].endswith('.clickData') and species_click:
            label=species_click['points'][0]['label']
            candidate={'type':'species_set','species':[s for s in trees.species.unique() if s not in labels],'label':label} if label=='其他樹種（合計）' else {'type':'species','species':label,'label':'樹種：'+label}
        elif species_relayout and 'hiddenlabels' in species_relayout:
            hidden=species_relayout['hiddenlabels']
            selected=[s for s in trees.species.unique() if (s in labels and s not in hidden) or ('其他樹種（合計）' not in hidden and s not in labels)]
            result={'type':'species_set','species':selected,'label':'樹種圖例篩選'} if hidden else {}
            return result,None,None,None,None,None
    else:
        result=update_drill_filter(*basics)
        return (no_update,)*6 if result is no_update else (result,None,None,None,None,None)
    if candidate is None:return (no_update,)*6
    comparable=lambda value:{k:v for k,v in value.items() if k!='label'}
    result={} if comparable(current)==comparable(candidate) else candidate
    return result,None,None,None,None,None

def local_time(value):
    stamp=pd.to_datetime(value,errors='coerce',utc=True)
    return '未提供' if pd.isna(stamp) else stamp.tz_convert('Asia/Taipei').strftime('%Y-%m-%d %H:%M')


def civic_rows(tab,population,district):
    tree_columns=population[['tree_id','tree_code','tree_kind','district','road','species']]
    if tab=='petitions':
        data=CIVIC['petitions'].copy()
        if district!='all' and len(data):
            data=data[data['address'].astype(str).str.contains(district,regex=False)]
        columns=[('address','地點'),('origin_label','來源'),('status_label','狀態'),('seed_count','已種苗數'),('threshold','門檻'),('species_summary','樹種意向'),('created_at','建立時間')]
    elif tab=='claims':
        data=CIVIC['claims'].merge(tree_columns,on='tree_id',how='inner')
        columns=[('tree_code','樹木編號'),('tree_kind','類型'),('district','行政區'),('road','路段'),('species','樹種'),('claimed_at','認領時間'),('expires_at','到期'),('checkin_streak','連續簽到')]
    elif tab=='events':
        data=CIVIC['events'].merge(tree_columns,on='tree_id',how='inner')
        columns=[('created_at','時間'),('tree_code','樹木編號'),('district','行政區'),('species','樹種'),('kind_label','事件'),('title','標題'),('detail','說明'),('actor_label','來源')]
    else:
        data=CIVIC['status'].merge(tree_columns,on='tree_id',how='inner')
        columns=[('tree_code','樹木編號'),('tree_kind','類型'),('district','行政區'),('road','路段'),('species','樹種'),('status_label','樹況'),('reason_label','原因'),('unit_name','管理單位'),('watch_count','集氣'),('updated_at','更新時間')]
    data=data.copy()
    for column in ('created_at','claimed_at','expires_at','updated_at'):
        if column in data:data[column]=data[column].map(local_time)
    return data,columns


@app.callback(Output('civic_claims','children'),Output('civic_watch','children'),Output('civic_status','children'),Output('civic_petitions','children'),Output('civic_summary','children'),Output('civic_table','data'),Output('civic_table','columns'),Input('civic_tab','value'),*analysis_inputs)
def render_civic(tab,*filters):
    population=ensure_civic_columns(selected_report_data(*filters)[0])
    district=filters[0]
    claims=int(population['claimed'].sum())
    watch=int(population['watch_count'].sum())
    unhealthy=int((~population['tree_status'].isin(['健康','無紀錄'])).sum())
    petitions=CIVIC['petitions']
    if district!='all' and len(petitions):
        petitions=petitions[petitions['address'].astype(str).str.contains(district,regex=False)]
    open_slots=int(petitions['status'].eq('open').sum()) if len(petitions) else 0
    data,columns=civic_rows(tab,population,district)
    summary=f'真實資料｜符合 {len(data):,} 筆' if CIVIC_READY else '公民護樹資料尚未連線'
    frame=data.reindex(columns=[c for c,_ in columns])
    rows=frame.head(1000).astype(object).where(pd.notna(frame.head(1000)),'未提供').to_dict('records')
    return f'{claims:,}',f'{watch:,}',f'{unhealthy:,}',f'{open_slots:,}',summary,rows,[{'id':c,'name':n} for c,n in columns]


if __name__=='__main__':
    app.run(debug=False,host='127.0.0.1',port=8050)
