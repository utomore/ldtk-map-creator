# Law

這個專案必須成立的性質（P-003）。

- 編號 `<前綴>-<n>`，不重用；測試用 `law("<編號>")` 標記。
- 一條 law 一句話：在什麼範圍內、什麼一定成立。

## GEN：生成

- **GEN-1**：同一份規則（含 seed）生成並寫出兩次，所有檔逐位元組相同；每個 entity 的 iid 由 seed 加元素名稱推出。
- **GEN-2**：生成成功時，每個元素都在它所在的 level 裡；區域與路徑都在 level 內、同一個 level 裡彼此不重疊；靠近別人的區域跟其他區域至少隔一格（彼此靠近的兩個除外）、跟靠近的對象剛好隔 `gap` 格；路徑接得上起點，另一頭接得上終點或通到那一邊；錨點在元素自己的格裡，路徑的 start 靠起點、end 靠終點；每一層（Collision、Slope、Height、Ground）每格是佔它的元素的圖格種類寫進那一層的值，沒寫或沒被佔的是 0；各層的值定義來源固定：Ground 照 `[grounds]`、Collision 照 `[collisions]`、Height 是 1 到圖格種類用到的最大階數、Slope 只有 1；每個 entity 擺在它的錨點或指定的格，不跟同一個 level 的 entity 擺在同一格；圖格種類帶 `part` 的每個區域，中心（`center` 錨點）那一格有一個對應的 `Part`，`part`、`yaw` 照種類，也不跟同一個 level 的別的 entity 同格（D-013）；每個 entity 的類型、欄位值照規則：規則寫的欄位是那個值，沒寫的是預設值；宣告的 entity 類型生成為同名、同顏色、同大小、同欄位、同 tags 的定義，擺出去的實例 `__tags` 跟定義相同；每個 level 寫在它在世界佈局裡的位置。
- **GEN-3**：排不下時丟 SceneError，訊息開頭是出錯的元素與欄位；斜坡種類的元素有一格四周（北東南西）沒有 `Height` 剛好高一階的格時也丟 SceneError，訊息開頭是那個元素的 `.kind`（D-012）；entity 或傳送點擺在帶零件區域的中心（零件的格）時也丟 SceneError，訊息開頭是擺過去的那一個的 `.at`、`.from` 或 `.to`（D-013）；擺在錨點的 entity，整個矩形（含 `offset`、`size`）超出 level 時也丟 SceneError，訊息開頭是 `.at` 或 `.size`（D-014）；傳送點的固定端點超出 level、level 不存在或沒寫（有好幾個 level 時），或蓋到同一個 level 別的 entity、零件或端點佔的格時也丟 SceneError，訊息開頭是 `<傳送點>.from` 或 `<傳送點>.to`（端點出界或 level 不對時多一層：`.at`、`.size`、`.level`）（D-014）；沒經過規則檔檢查、直接組出來的 `offset`、`size`、端點與慣例 entity 的覆寫不合法時也一樣（D-014）；丟錯時還沒寫任何檔。
- **GEN-4**：改一個區域的一個欄位（位置、大小、靠近的一邊、間隔），其他元素只有在指向已移動的元素、或新舊兩次排列中被已移動的元素擋過時才會移動。
- **GEN-5**：生成操作寫完之後，生成紀錄記下的內容等於磁碟上場景地圖的內容；規則與場景地圖都沒變時再生成一次，磁碟上的檔完全不變。
- **GEN-6**：生成之後直接改過場景地圖（畫格、擺放、移動、刪除 entity、新增 level），不帶 force 的重新生成一律拒絕、磁碟上的檔不變、訊息列出差異；帶 force 就照規則蓋掉，結果跟剛生成時相同。
- **GEN-7**：世界佈局裡 level 彼此不重疊；用 `at` 的 level 在指定的世界座標（第一個沒寫就在 (0, 0)）；用 `near` 的 level 跟對象剛好隔 `gap` 格、面對面（不是斜對角），寫了 `side` 就在那一邊。
- **GEN-8**：每個傳送點在兩端的錨點（或 GEN-11 的固定端點）上各生成一個 `Teleport`（不寫類型），兩端不在同一格、也不跟同一個 level 的別的 entity 同格；兩個 `Teleport` 的 `targetPort` 互指（可以跨 level），iid 由 seed 加傳送點名稱推出。
- **GEN-9**：規則檔頂層的 `grid`（沒寫是 16）決定格子大小：生成的專案檔 `defaultGridSize`、每一層的 `gridSize` 與 `__gridSize` 都等於它，LDtk 新增 level 的預設大小是 16 格見方、新增 entity 定義的預設是一格大；每個 level 的像素大小與世界座標是格數乘以它；沒寫 `size`、`offset` 的 entity（GEN-10）與不是固定端點的傳送點（GEN-11），`px` 是格座標乘以它加上 pivot 的偏移，大小等於它的定義；範本三種 entity 定義與沒寫 `size` 的宣告類型是一格大（`grid`×`grid`），寫了 `size` 的照寫（像素）；生成的場景地圖通過慣例檢查與官方 schema。
- **GEN-10**：`[[entity]]` 寫了 `size`（格）、`offset`（像素）時，生成的實例 `width`、`height` 是 `size` 乘以格子大小（沒寫是類型定義的大小），`px` 是它所在那一格的左上角加上 `offset`，`__grid` 還是那一格；每個擺在固定格或錨點的 entity，整個矩形（`px` 加上 `width`、`height`）都在它的 level 內；任何 entity 定義，只要有實例的大小跟它不同，`resizableX`、`resizableY` 就是真（LDtk 才會保留實例的大小），否則維持範本的值（假）；定義自己的大小、其餘欄位不因此改變。
- **GEN-11**：傳送點的端點是固定端點 `{ level, at, size }` 時，那一端的 `Teleport` 在那個 level（沒寫是第一個）的 `at` 格，`width`、`height` 是 `size`（沒寫是 [1, 1]）乘以格子大小，`px` 是 `at` 乘以格子大小；端點的每一格都不跟同一個 level 別的 entity、區域零件或端點佔的格重疊（端點可以蓋在區域與路徑的格上）；兩端互指與其他規則照 GEN-8。
- **GEN-12**：規則檔的 `[entity_types]` 覆寫分層慣例 entity（`PlayerSpawn`、`Teleport`、`Part`）時，定義的顏色、tags 是覆寫的值（沒寫的維持範本的值），實例的 `__tags`、`__smartColor` 跟著定義；定義的名稱、大小、欄位定義跟沒覆寫時完全一樣，別的 entity 定義不受影響；生成的場景地圖通過慣例檢查（D-011）。
- **GEN-13**：規則檔頂層的 `split` 有寫時，生成之後的存法就是它（`true` 拆開、`false` 單檔，子資料夾裡只有拆開存時才有 `.ldtkl`），不看磁碟上原本怎麼存、主檔在不在（主檔不在、子資料夾裡還有別人的 `.ldtkl` 時，照舊不覆蓋）；沒寫時沿用磁碟上的存法（沒有主檔可以看時拆開）；兩種存法讀回來的場景地圖內容相同，除了 `externalLevels`；存法改變的途中失敗，磁碟上的檔（含 `.ldtkl` 與子資料夾）不變（SCENE-5）；存法沒變、規則與場景地圖都沒變時再生成一次，磁碟上的檔完全不變。

## RULE：規則檔

- **RULE-1**：合法的規則檔讀進來不報錯；頂層的 `grid`（1 到 1024）照檔案保留（選填，沒寫是 16）；每個欄位的值與同一種元素、level、entity 類型、地面種類、實心種類、圖格種類的順序、以及 entity 類型的 `tags`（沒寫是空的）、`[[entity]]` 的 `fields`（欄位名稱與值）、`size`（格，沒寫是沒有）、`offset`（像素，沒寫是 (0, 0)）、`[[portal]]` 的 `from`、`to`（錨點，或固定端點 `{ level, at, size }`：`size` 沒寫是 [1, 1]、只有一個 level 時 `level` 可以不寫）、`[entity_types]` 裡分層慣例 entity 的覆寫（`color`、`tags`，沒寫的是沒有）、頂層的 `split`（沒寫是不指定）都照檔案保留（圖格種類的 `ground`、`collision`、`height`、`slope`、`part`、`yaw` 都是選填，沒寫的取預設值：沒有、沒有、0、假、沒有、0；`yaw` 整數也收，存成小數）；只有一個 level 時，`[level]` 與 `[[level]]` 兩種寫法讀出來一樣。
- **RULE-2**：規則檔有一處不合法（未知或缺少的欄位、`grid` 不是 1 到 1024 的整數（含布林、小數、字串）、型別不對、尺寸小於 1、固定的位置或大小超出 level、元素名稱重複、指向不存在的元素、種類、錨點或 level、指向別的 level 的東西（傳送點除外）、有好幾個 level 卻沒寫元素在哪個 level、路徑起點等於終點、元素或 level 互相指向成環、level 名稱只差大小寫、`[[portal]]` 寫了 `type`、entity 類型的 `tags` 不是字串陣列、是空字串或只有空白的字串、或重複、`[[entity]]` 的 `fields` 欄位不存在或值的型別不對（Int、Float、Bool、String；Float 也收整數）、`fields` 裡寫 EntityRef 欄位、`[[entity]]` 的類型是 `Teleport`（要成對，用 `[[portal]]`）、`Part` 沒寫零件名稱或 `part` 是空白、傳送點兩端是同一個錨點或同一個區域的同一格、用 at 固定位置的 level 互相重疊、`[grounds]` 或 `[collisions]` 的值小於 1 或同一節內重複、顏色不是 #RRGGBB、圖格種類一層都沒寫進也沒帶零件（四個欄位和 `part` 都沒寫，或只寫了 `height = 0`、`slope = false`）、`part` 不是字串或是空白、`yaw` 不是有限的數字（含布林）、只寫 `yaw` 沒寫 `part`、路徑用了帶零件的種類、entity 或傳送點的錨點指到帶零件區域的 `.center`（零件的格）（D-013）、`ground` 或 `collision` 不是宣告過的名稱、`height` 不是 0 到 64 的整數、`slope` 不是布林、圖格種類出現舊寫法的 `value` 或 `color`、`[[entity]]` 的 `offset` 不是各在 0 到 grid - 1 的兩個整數、`size` 不是至少 1 的兩個整數、擺在固定格的 entity 整個矩形（含 `offset`、`size`）超出 level、固定端點缺少 `at`、有不認得的欄位、level 不存在或有好幾個 level 卻沒寫、位置或大小超出 level、兩端都是固定端點而兩個矩形重疊（D-014）、分層慣例 entity 的覆寫寫了 `color`、`tags` 以外的欄位、兩個都沒寫、名稱的大小寫不對、`color` 不是 #RRGGBB、`tags` 不合法、`PlayerSpawn` 的 tags 沒有 `player`（D-011、D-014）、`split` 不是布林），讀取丟 SceneError，訊息開頭寫出出錯的元素與欄位，例如 `house_2.size`、`oak_1.fields.yaw`。

## FILE：讀寫檔

- **FILE-1**：repo 裡的範本，以及工具寫出的每個場景地圖檔，都通過官方 LDtk 1.5.3 JSON schema；`.ldtkl` 去掉 LDtk 自己加的 `__header__` 後，以 schema 的 `Level` 驗證。
- **FILE-2**：寫出再讀回的場景地圖裡，圖層定義、entity 定義、欄位定義、level 的 `uid` 互不重複，而且都小於 `nextUid`；所有 `iid` 互不重複。
- **FILE-3**：讀進來、寫回去、再讀一次，內容跟寫之前完全相同，包括範本裡程式沒宣告的欄位。
- **FILE-4**：寫出的每個 IntGrid 圖層，`__cWid` 等於 `ceil(pxWid / gridSize)`、`__cHei` 同理，`intGridCsv` 長度等於 `__cWid × __cHei`。
- **FILE-5**：拆開存時，主檔每個 level 的 `layerInstances` 是 `null`、`externalRelPath` 指向存在的 `.ldtkl`；子資料夾裡沒有對不上 level 的 `.ldtkl`；單檔存時，子資料夾裡沒有任何 `.ldtkl`。
- **FILE-6**：寫出的 `__neighbours` 由 level 的世界座標決定，而且對稱：A 把 B 列為某個方向，B 就把 A 列為反方向（n↔s、w↔e、nw↔se、ne↔sw、o↔o、<↔>）；不相接的 level 不列；線性佈局（LinearHorizontal、LinearVertical）一律不列，照 LDtk 1.5.3 的 `Level.getNeighboursJson`。

## SCENE：場景地圖

- **SCENE-1**：IntGrid 每格的值都是 0 或該層定義宣告過的值；畫未宣告的值會被拒絕，場景地圖不變。
- **SCENE-2**：畫格只改指定的格：範圍內都是指定值，範圍外不變；回傳的格數等於實際變動的格數。
- **SCENE-3**：超出 level 範圍的畫格、擺放、移動會被拒絕，場景地圖不變。
- **SCENE-4**：擺放或移動到 (x, y) 格的 entity，寫出再讀回後 `__grid` 是 [x, y]，不論 pivot；刪除後它的 iid 不在場景地圖裡。
- **SCENE-5**：每個會改動場景地圖的操作完成後，磁碟上的場景地圖重新讀進來就是操作後的狀態，那個 level 的預覽圖與世界總覽等於重新 render 的結果；操作失敗時，磁碟上的檔不變。
- **SCENE-6**：擺放、設定欄位、移動、刪除 entity，以及生成時 `[[entity]]` 帶的欄位值，寫出再讀回之後，每個 entity 的欄位實例跟它的定義一一對應（順序、名稱、型別）；`__value` 是設定的值，沒設的是預設值；EntityRef 的 `__value` 指向的圖層與 level 就是目標實際所在的位置，可以跨 level。
- **SCENE-7**：刪掉一個 entity 之後，沒有 EntityRef 指向它；欄位不存在、型別不對、不能是空的卻給空值、EntityRef 指向不存在的 entity，都會被拒絕，場景地圖不變。
- **SCENE-8**：新建的場景地圖與生成的場景地圖，圖層定義由上到下是 `Entities`、`Collision`、`Slope`、`Height`、`Ground`（D-011），entity 定義有 `PlayerSpawn`（tags `player`）、`Teleport`（`targetPort`，EntityRef）、`Part`（`part` 字串、`yaw` 浮點）；每個 level（含新增的）都有這五層的實例；規則宣告的 entity 類型的 tags 照宣告（沒寫是空的）；規則檔覆寫的慣例 entity 照 GEN-12。

## CONV：分層慣例

- **CONV-1**：新建的場景地圖（含新增的 level）通過慣例檢查；生成成功的場景地圖一律通過（D-012）。
- **CONV-2**：場景地圖有以下任何一處違反，檢查都會報出來，每一處一行，開頭寫出 level、層或 entity 與位置：層定義缺少、重複、型別不對；`Slope` 宣告了 1 以外的值；`Height` 宣告了小於 1 的值；level 缺少某一層的實例；IntGrid 格的值不是 0 也沒宣告；`Slope` 不是 0 的格四周（北東南西）沒有 `Height` 剛好高一階的格；`PlayerSpawn` 缺少定義或 tags `player`；`Teleport` 缺少定義或 `targetPort`（EntityRef）；`Part` 缺少定義或 `part`（String）、`yaw`（Float）；`Teleport` 的 `targetPort` 是空的、指到自己、指到不存在的 entity 或不是 `Teleport` 的 entity；`Part` 的 `part` 是空白的。同一份場景地圖查兩次，輸出完全相同（順序照定義，再照 level、層、格）；檢查不改場景地圖，操作也不寫任何檔、不出預覽圖。
- **CONV-3**：慣例是下限：多一層、IntGrid 層多宣告的值（`Slope` 只能是 1、`Height` 要 ≥ 1）、entity 定義多的 tags 與欄位、多的 entity 定義與實例，都不報。

## ENTRY：入口

- **ENTRY-1**：`core/actions.py` 的每個操作，都有同名的 MCP 工具與 CLI 指令，沒有多也沒有少。

## RENDER：預覽圖

- **RENDER-1**：PNG 的尺寸是座標邊框加上 level 像素乘以倍率：`(MARGIN + pxWid × scale, MARGIN + pxHei × scale)`；倍率沒給時，格子 32 像素以下是 2、更大是 1。
- **RENDER-2**：沒被 entity 或名稱蓋住的 IntGrid 格，格子中心的顏色是看得見的最上層非 0 值定義的顏色（圖層不透明時）；圖層半透明（displayOpacity 小於 1）時，跟下面看得見的顏色按透明度混合；全部是 0 的格是 level 背景色。
- **RENDER-3**：每個 entity 方塊中心的顏色是它定義的顏色。
- **RENDER-4**：同一個環境裡，同一份場景地圖 render 兩次、或寫出再讀回後 render，PNG 的位元組完全相同；render 不改動場景地圖。
- **RENDER-5**：給了元素名稱時，每個名稱的標示位置都在元素自己的格上；名稱整個留在 level 範圍內、彼此不重疊（靠邊往內推，太長或會撞到就縮小字，再放不下就不寫），放得下就以標示位置為中心，會蓋到 entity 方塊（連同方塊上方的 entity 名稱）就上下挪一兩格、挪到不蓋為止，怎麼挪都會蓋到才照原位；字是淺色加深色描邊，畫在格線上、entity 下，格子座標最後畫；同樣的名稱 render 兩次位元組相同。只有地圖跟生成紀錄一致時才給名稱：沒有紀錄、紀錄讀不了或地圖被直接改過都不寫。
- **RENDER-6**：世界總覽裡每個 level 照世界座標等比例縮小（長邊最多 1600 像素；線性排法照 level 的順序接著排）畫在對的位置與大小；放得下就標出 level 名稱，不跑出 level 的外框；每個 EntityRef（含陣列）的兩端之間畫一條線，互指的一對只畫一條；同一份場景地圖 render 兩次位元組相同。
