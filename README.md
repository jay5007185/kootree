# 臺北市智慧行道樹管理平台 — MVP

## 樹靈記 App 公民護樹資料（新增）

`dashboard_supabase.py` 現在同時讀取 `tree_locations` 的行道樹（`street`）與文化局受保護樹木（`protected`），並加入樹靈記 App（PlantyFun）使用者產生的公民護樹資料，全部只讀：

| 資料 | 來源表 | 呈現位置 |
| --- | --- | --- |
| 市民通報 | `tree_reports` | 既有通報圖表；App 的類型代碼（`disease_sign` 等）顯示為中文 |
| 樹靈認領 | `tree_claims`（只算未到期） | 公民護樹區塊、樹木資訊、地圖「護樹狀態」圖層 |
| 樹況與集氣 | `tree_status_view` | 同上；樹況只有已驗證管理單位帳號能改 |
| 時間軸 | `tree_events` | 公民護樹區塊、案件資料中心的逐樹履歷 |
| 請願空位 | `petition_slot_summary` | 公民護樹區塊，依地址比對行政區 |

這些表的 RLS 開放匿名讀取，publishable key 即可。任何一張讀不到只會在該區塊標示缺漏，不影響其他資料。欄位對應與中文標籤在 `scripts/civic_data.py`。

### Supabase I/O 用量

讀取程式改為只在第一頁要求 `count=exact`、其後用 keyset 分頁（`scripts/supabase_data.py`），`kootree_rows` 快照改用 `ordinal` keyset 取代 `offset`（`scripts/supabase_store.py`），`app.py` 預設關閉 Dash 的 `debug` 自動重載。每次啟動仍會把約 9.7 萬棵樹整張讀完，請避免頻繁重啟。

## Supabase 即時樹籍版本（新增）

本次新增 `dashboard_supabase.py`，直接唯讀查詢既有的 `tree_locations`、`tree_reports`、`dashboard_demo_reports`、`tree_species_care_profiles` 與 `tree_species_care_rules`，以及上一節的公民護樹資料表。保留原 `app.py` 的官方移除／移植、資料中心、品牌及快照功能，兩個入口可分別啟動。

```bash
pip install -r requirements.txt
cp .env.example .env
# 填入 SUPABASE_KEY；不要提交 .env
python dashboard_supabase.py
```

兩個入口預設使用 8050 埠，請擇一啟動。新入口不回退至本機 CSV，不會自動生成虛構案件；真實／模擬通報可切換。樹木編號顯示 `code`，UUID 僅作內部關聯；胸徑、樹高顯示雲端來源值，未以建檔日期冒充調查日期。

月份規則已提供獨立 SQL 與來源說明，見 [全樹種養護月份維護](docs/species-care-calendar.md)。養護卡片與路段卡片支援再次點擊取消篩選。介面依照 https://kootree-hanitu.nelsliu9121.workers.dev/dashboard/ 的 KooTree 品牌版本調整：插畫頁首、米白／森林綠配色、植物線條圖示、區塊導覽與地圖工具列。摘要圖採 TOP 10 加其他的樹種圓環圖、完整 12 區棵數及占比，沿用即時篩選結果。新入口樣式隔離於 `supabase_assets/`，不覆蓋原站樣式。

本機環境實測 93,045 筆行道樹及 200 筆獨立模擬通報；實際數量以各部署的 Supabase 專案與讀取權限為準。

完整參考版整合已補齊地圖五圖層、官方紀錄讀取、六張連動圖表、案件與養護資料中心及目前分頁匯出，見 [完整後台與資料差異說明](docs/dashboard-reference-parity.md)。官方資料匯入 SQL 為 `supabase/seed_dashboard_official_cases.sql`；工單採獨立資料表，未提供紀錄時不生成假資料。

原 app.py 的 Supabase 匯入與讀取設定請見 [串接說明](docs/supabase-setup.md)。預設仍使用本機 CSV；填妥後端環境設定並完成資料匯入後可切換至 Supabase。

以 **Dash + Plotly + Pandas + pyproj** 製作的可執行 GIS 管理後台。

## 功能

- 行政區、路段、樹種、觀察月份篩選
- 問題通報類型、案件狀態篩選
- OpenStreetMap 互動地圖、樹種圖層、月份養護關注圖層、通報熱點及風險分級圖層
- 點選標記查看樹木個別資料、通報摘要與示範風險分級
- 樹種 TOP 10、行政區分布、問題類型、行政區每千棵通報率圖表
- 優先巡檢路段清單與待處理通報、高風險樹木、平均案件天數 KPI
- 點選當月養護卡片、優先巡檢路段卡片或問題通報長條圖，可同步鑽取更新地圖、KPI、圖表、明細與匯出資料
- 明細表格及篩選結果 CSV 匯出
- 官方 TWD97 (EPSG:3826) 座標轉 WGS84 (EPSG:4326)

## 快速啟動

要求 Python 3.10+。

```bash
cd taipei_tree_mvp
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

瀏覽器開啟 http://127.0.0.1:8050 。首次啟動若無官方資料，會使用**明確標註為虛構**的示範資料。地圖底圖需要網路連線。

## 使用臺北市政府真實資料

官方來源：https://data.taipei/dataset/detail?id=7a49d00c-a5ff-4a6b-be9e-aaa6dc1ff7e8

方法 A：自動下載與轉換（網路可連線時）：

```bash
python scripts/data_loader.py --download
python app.py
```

方法 B：進入官網下載「行道樹資料_CSV」，放在 `data/taipei_tree_raw.csv`，執行：

```bash
python scripts/data_loader.py
python app.py
```

方法 C：指定檔案：`python scripts/data_loader.py --input /path/to/TaipeiTree.csv`

載入官方資料後，重新啟動應用，即會自動使用 `data/trees_clean.csv`。如需切換回示範資料，可移走 `data/trees_clean.csv`。

> 備註：來源站臺資源網址在 2026-10-20 驗證為 `https://tppkl.blob.core.windows.net/blobfs/TaipeiTree.csv`，若未來更改，請改以官網手動下載。

## 資料限制

官方 CSV 欄位為 TreeID、Dist、Region、RegionRemark、TreeType、Diameter、TreeHeight、SurveyDate、TWD97X、TWD97Y、Update。**不包含案件通報、巡檢風險、庇蔭率或實際養護狀態**。

為了讓黑客松展示完整呈現「市府後台如何用資料決策」，本 MVP 會在前端執行時加入清楚標註的示範推估欄位：

- `issue_type`：問題通報類型示意，例如落枝枯枝、側枝遮擋、根系隆起。
- `issue_status`：案件狀態示意，例如待巡檢、處理中、已結案。
- `risk_score` / `risk_level`：依胸徑、樹高、通報狀態與樹種加權產生的展示分數。
- `case_age_days`：案件天數示意。

這些欄位不是官方資料、不是樹木安全診斷，也不能作為實際養護指令。正式系統應改由陳情案件、巡檢紀錄、養護工單與樹木專業評估表匯入。

月份提示以少量示意物候規則產生，並非確定的病蟲害／安全預警。

為保持大資料量地圖流暢，地圖逐棵顯示全部樹木，不抽樣、不群聚；移動或縮放後載入目前視窗範圍內的完整點位；KPI、圖表及匯出仍以全部篩選資料計算；表格畫面最多顯示前 1,000 筆，匯出包含全部。

## 專案目錄

```
taipei_tree_mvp/
├── app.py                       # Dash 主程式
├── assets/style.css             # 介面樣式
├── scripts/data_loader.py       # 官方 CSV 轉換
├── scripts/generate_demo.py     # 虛構展示資料產生器
├── data/demo_trees.csv          # 虛構展示資料
├── tests/test_data_loader.py    # 轉換單元測試
├── requirements.txt
└── README.md
```

## 下一步

可以新增公務案件表、巡檢歷史、樹冠 GIS Polygon、里鄰邊界，以及 PostGIS 空間索引與工單權限管理。現在 MVP 不包含認證與權限控管，**不應直接暴露於公網作正式機關系統**。

## 模擬案件與養護資料中心

點選「待處理通報」或「高風險樹木」數字卡片，會捲動至下方資料中心並開啟對應分頁。另有「養護工單」及「歷史趨勢」分頁；各分頁依左側篩選及圖卡鑽取條件更新。點選清單任一列可查看樹木的模擬巡檢、通報、派工與結案履歷，可匯出目前分頁 CSV。

模擬紀錄由 `scripts/simulate_operations.py` 以固定種子產生，基準日為 2026-10-21。歷史圖呈現截至該日六個月份的模擬新增與結案數，最後一個月為截至基準日的部分月份。日期、班組、養護項目與案件均非官方紀錄；本版本不會保存使用者編修或累積真實歷史。畫面清單最多顯示 1,000 筆，統計和匯出使用全部符合資料。

## 分析圖表

樹種圖顯示前十名與其餘樹種合計，直接標示棵數及占目前篩選結果的比例。行政區圖保留資料集全部 12 區，無符合資料的區域顯示 0。模擬通報類型使用狀態堆疊長條；模擬通報率使用點圖與目前篩選結果的整體加權通報率參考線，滑鼠移入可查看樹木分母及通報數。

新增路段巡檢散點圖（待處理件數 × 高風險棵數，圓點大小為路段樹木數）及待處理案件天數分布。散點圖點選路段可連動篩選，再次選取相同路段取消。風險、通報、案件天數均屬模擬，天數分組不代表服務期限；目前生成天數為 1–21 天，因此 31 天以上可能為 0。


## 官方移除與移植公開資訊

新增「樹木移除與移植 · 官方公開資訊」區塊，資料來自臺北市公園處「小樹保衛站」公開 GeoJSON：
https://treesmatch.gov.taipei/pkl/public/tree-map/api/clarifications.geojson

- 保存於 `data/treesmatch-clarifications.geojson`；來源、下載時間與授權記錄於 `data/treesmatch-provenance.json`。
- 目前快照包含 76 則公開說明，66 則具概略座標，另有 20 筆目的地描述（含未提供座標者）。資料不是全市逐株移除／移植清冊，筆數不是株數；不能從說明篇數推算移除數量。
- 「提及移除／移植」是原文關鍵詞篩選，包含澄清、規劃與完成事項，不推定作業狀態。
- 官方資料與模擬通報分開呈現，不以地點近似強行對應行道樹編號。
- 樹形群聚標記、放大展開、點選原文、關鍵詞搜尋、清單選取、CSV 匯出、移植目的地與關係連線。虛線不代表實際運送路線，缺失座標不自行補造。
- 此區篩選獨立於行道樹篩選，地圖需連網載入 OpenStreetMap 圖磚；Leaflet 1.9.4 和 MarkerCluster 1.5.3 已存於 `assets/vendor/`，授權檔同目錄。
- 官方開放資料授權：政府資料開放授權條款第 1 版（https://data.gov.tw/license）。

手動更新官方快照後重新啟動網站：

```powershell
.\.venv\Scripts\python.exe scripts/official_cases.py --download
.\.venv\Scripts\python.exe app.py
```

下載失敗或回傳格式錯誤時保留前次快照。網站不在啟動時自動向官方發送資料請求。

補充資料採新增方式：原有行道樹、KPI、圖表與明細保留；原地圖可勾選「加疊官方移除／移植相關說明」。此圖層預設關閉，標示全市概略位置，不依地點相近合併資料，不計入行道樹株數或原模擬通報統計。上方提供「行道樹總覽」與「新增：移除／移植官方資訊」區塊導覽。

### 單一地圖工作區

行道樹與官方公開說明共用「樹木分布地圖」；官方圖層可獨立開關。右側以「行道樹資料／官方移除與移植」頁籤閱讀資訊，點選官方標記會載入完整原文。官方主題、搜尋及 CSV 清單收於同一地圖下方的可展開面板，不再顯示第二張獨立地圖。選定案件時只顯示該案已提供座標的目的地與關係連線，缺失座標保留文字。資料筆數及行道樹株數分別計算。
