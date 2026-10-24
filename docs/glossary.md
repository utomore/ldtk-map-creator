# 名詞表

這個專案的用詞，新加入的人與 LLM 都用同一套。「名詞」與「定義」是對使用者講的話，「識別名」是程式碼裡用的名字。

- 程式碼要用一個新的名詞命名，先在這裡登記。
- 定義寫「它是什麼、不是什麼」，一句話。
- 還沒有程式碼用到的名詞，識別名填 `-`，等實作的人決定。

| 名詞 | 定義 | 識別名 |
|---|---|---|
| 場景地圖 | 一個 LDtk 專案，代表一整個場景（例如村莊），裡面有多個 level；不是單一 `.ldtk` 檔 | `SceneMap` |
| 規則檔 | 記下場景地圖生成規則與 seed 的檔案，用來重現生成結果；不是微調紀錄 | `Rules`、`core/rules.py` |
| 元素 | 規則檔裡一個有名字的項目，對應地圖上的一塊東西：區域、路徑或 entity；不是 LDtk 的 entity 定義 | `AreaRule`、`PathRule`、`EntityRule` |
| 不合格的斜坡 | `Slope` 層畫了的格，北東南西都沒有 `Height` 剛好高一階的格；引擎長不出斜面，生成會擋下、慣例檢查會報（D-012）；不是畫錯值 | `stranded_slopes`；測試裡的 `stranded_elements`、`qualified_slopes` |
| 錨點 | 元素上可以讓別的元素指向的位置，例如 `main_road.end`；不是 entity 的 pivot | `AREA_ANCHORS`、`PATH_ANCHORS` |
| 圖格種類 | 規則檔 `[kinds]` 裡一個有名字的項目，寫明元素畫進分層慣例的哪幾層、各是什麼值（D-011）；不是工作的種類 | `Kind` |
| 區域零件 | 圖格種類帶 `part`（與 `yaw`）時，生成在用了這個種類的區域中心的那個 `Part` entity（D-013）；不是規則檔 `[[entity]]` 擺的 `Part` | `Kind.part`、`Kind.yaw`、`Layout.parts`、`area_part_iid` |
| 地面種類 | 規則檔 `[grounds]` 裡一個有名字的 `Ground` 層的值與預覽顏色，遊戲對到材質；不是圖格種類 | `LayerValue`（`Rules.grounds`） |
| 實心種類 | 規則檔 `[collisions]` 裡一個有名字的 `Collision` 層的值與預覽顏色，例如牆、水；不是圖格種類 | `LayerValue`（`Rules.collisions`） |
| 方位 | 規則檔裡的東南西北：`side` 指區域在誰的哪一邊，`edge:<方位>` 指 level 的哪一邊；不是 LDtk 的 `__neighbours` 方向 | `Side`、`SIDES` |
| 格 | level 上一格 IntGrid 的位置或大小，以格為單位、原點在左上角；不是像素 | `Cell` |
| 生成 | 照規則檔建出一個場景地圖：先配置，再畫進 level；不是 LDtk 的 auto-layer | `generate_scene` |
| 生成紀錄 | 上次生成的結果，存在 `<名>.generated.json`，重新生成前拿來比對地圖有沒有被直接改過；不是微調紀錄 | `core/record.py` |
| 世界佈局 | 場景地圖裡每個 level 在世界上的位置（格），彼此不重疊；規則檔用 `at` 或 `near` 寫（D-009）；不是 LDtk 的 worldLayout 設定 | `place_levels`、`Layout.levels` |
| 配置器 | 照規則算出每個元素佔哪些格、錨點在哪的計算；只算位置，不碰場景地圖 | `layout`、`Layout` |
| 排列順序 | 配置器排元素的先後：先排固定位置的區域，再排指向的東西都排好了的，路徑優先；只看規則，不看位置 | `placement_order` |
| 推出的 iid | 由 seed 加名字算出的 iid（uuid5），同規則同 seed 就相同；不是 LDtk 隨機配的 iid | `derived_iid`、`element_iid` |
| 擠開 | 配置時一個元素的候選位置被別的元素佔了，只好換位置；記下被誰擠開，用來說明改一個元素會牽動誰 | `pushed_by` |
| entity 類型 | 規則檔 `[entity_types]` 裡宣告的一種 entity：顏色、大小、欄位與 tags，生成時變成 LDtk 的 entity 定義；不是元素 | `EntityType` |
| 傳送點 | 規則檔 `[[portal]]` 元素：兩端各擺一個 `Teleport`，用 `targetPort` 互指，可以跨 level，不寫類型（D-011）；一端是錨點，或固定端點；不是 PlayerSpawn | `PortalRule` |
| 固定端點 | `[[portal]]` 的 `from`、`to` 寫成 `{ level, at, size }`：位置（格）與大小（格）都寫死的一端，`size` 沒寫是 [1, 1]（D-014）；不是錨點 | `FixedEnd` |
| 實例大小 | `[[entity]]` 的 `size`（格）：這一個 entity 實例的大小，沒寫就是類型定義的大小（D-014）；不是 `[entity_types]` 的 `size`（像素，類型的預設大小） | `EntityRule.size` |
| 格內位移 | `[[entity]]` 的 `offset`（像素，各在 0 到 grid - 1）：實例擺在那一格左上角往右下位移後的位置（D-014）；不是格座標 | `EntityRule.offset` |
| 慣例覆寫 | `[entity_types]` 裡用分層慣例 entity 的名字（`PlayerSpawn`、`Teleport`、`Part`）寫的 `color`、`tags`：只改外觀，名稱、大小與欄位不變（D-014）；不是宣告新類型 | `ConventionOverride`、`Rules.overrides` |
| 存法指定 | 規則檔頂層的 `split`：生成後一律拆開（`true`）或單檔（`false`），不看磁碟上原本怎麼存（D-014）；沒寫就沿用磁碟上的 | `Rules.split` |
| 欄位 | entity 上一個有名字、有型別的值（LDtk 的 field），例如傳送點的 `targetPort`；不是規則檔裡元素的欄位 | `FieldDef`、`FieldInstance`、`core/fields.py`；規則檔 `[[entity]]` 帶的欄位值是 `EntityRule.fields` |
| 微調 | 生成之後透過對話修改場景地圖；從規則生成的場景地圖，改的是規則檔再重新生成（D-008）。不是在 LDtk 裡手動改 | - |
| 分層慣例 | 固定的 IntGrid 層與 Entity 定義的命名和意義，讓引擎照著就能串接（D-011）；是下限，其他 LDtk 功能照用；不是某款遊戲專用的層名 | `core/convention.py` |
| 慣例檢查 | 讀一個場景地圖，列出每一處不照分層慣例的地方；唯讀，慣例是下限所以多的層、欄位、tags 不報；不是 LDtk 的 JSON schema 驗證（FILE-1），也不是跟生成紀錄比對有沒有被直接改過（GEN-6） | `check_scene`、`core/check.py` |
| level | 場景地圖裡的一張地圖，例如村莊外圍的一塊或一間房子的內部；沿用 LDtk 的用詞 | `Level`；規則檔裡的是 `LevelRule` |
| 操作 | 入口直接呼叫的單位：讀場景地圖、做一件事、寫回、更新預覽圖；一個操作對應一個 MCP 工具與一個 CLI 指令。不是 core 裡單純改記憶體的函式 | `core/actions.py` |
| 預覽圖 | 一個 level render 成的 PNG，存在 `previews/<主檔名>/<level>.png`；不是 LDtk 自己匯出的圖 | `render_level`、`write_preview` |
| 世界總覽 | 一張 PNG，把場景地圖的每個 level 照世界座標縮小畫在一起，標出 level 名稱、傳送點之間畫線；存在 `previews/<主檔名>/world-overview.png`；不是單一 level 的預覽圖 | `render_world`、`world_preview_path`、`WorldView`、`world_positions`、`world_links` |
| 範本 | 用 LDtk 1.5.3 存出的空專案，新的場景地圖與 level 從它複製；不是規則檔 | `core/templates/` |
