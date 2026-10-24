# 指定 entity 與傳送點的位置與大小

寫規則檔時，entity 與傳送點怎麼擺在固定的位置、給固定的大小，慣例 entity 怎麼改外觀，怎麼固定存成單檔（D-014）。

- 完整範例：`tests/data/placement.rules.toml`（`grid = 96`，兩個 level）。
- 單位：
  - `[[entity]]` 的 `size` 是格；`offset` 是像素。
  - `[entity_types]` 的 `size` 是像素（類型的預設大小）。

## 實例的大小：`size`

- 同一個類型的實例大小不一樣時，在 `[[entity]]` 寫 `size = [寬, 高]`（格）。
- 沒寫就是類型的大小；範本三種（`PlayerSpawn`、`Teleport`、`Part`）是一格。

```toml
[entity_types]
House = { color = "#b33831", size = [384, 288] }  # 像素：預設 4×3 格（grid = 96）

[[entity]]
name = "house_a"
type = "House"
at = [2, 2]                                       # 4×3 格

[[entity]]
name = "house_b"
type = "House"
at = [8, 2]
size = [3, 3]                                     # 這一間 3×3 格
```

- 有實例的大小跟類型不同時，生成的定義會設成可縮放，LDtk 才不會把實例改回定義的大小。

## 格內的位置：`offset`

- 實例要擺在格子裡的某個位置，不是左上角：寫 `offset = [x, y]`（像素，各在 0 到 `grid` - 1）。
- `px` 是那一格的左上角加 `offset`；位移小於一格，所以實例還在那一格。
- 實例比一格小（例如 8×8 像素）：`size` 最小是一格，要宣告一個 `[entity_types]` 類型寫像素大小，再用 `offset` 擺。

```toml
[[entity]]
name = "spawn"
type = "PlayerSpawn"
at = [4, 3]
offset = [48, 72]   # (4, 3) 那一格往右 48、往下 72：grid = 96 時 px 是 (432, 360)

[entity_types]
Scout = { color = "#f9c22b", size = [8, 8], tags = ["player"] }  # 像素：8×8，比一格小
```

## 整個矩形要在 level 內

- 每個 entity 的矩形（位置加 `offset`，大小是 `size` 或類型的大小）都要在 level 內，不只左上角。
- 錯誤開頭是元素與欄位：
  - `spawn.at：...`：位置加位移或大小超出 level。
  - `spawn.size：...`：`size` 本身比 level 還大。
- `at` 寫錨點時，矩形出不出界要排了才知道，生成時才報。
- 兩個 entity 擺在同一格的檢查只看左上角那一格；矩形大不算撞到。

## 固定位置與大小的傳送點

- `[[portal]]` 的 `from`、`to` 除了錨點，也可以寫固定端點 `{ level, at, size }`：`at` 是左上角的格，`size` 是格。
- `size` 沒寫是 `[1, 1]`；只有一個 level 時 `level` 可以不寫。

```toml
[[portal]]
name = "home_door"
from = { level = "Home", at = [0, 3], size = [1, 3] }   # 1×3 的出口
to = { level = "Farm", at = [0, 10], size = [1, 3] }
```

- 一端用錨點、一端用固定端點也可以：`from = "house_1.south"`、`to = { level = "Farm", at = [0, 10] }`。
- 端點的每一格都不能跟同一個 level 別的 entity（左上角那一格）、區域零件、別的端點撞到：
  - 撞到，報錯開頭是 `<傳送點>.from` 或 `<傳送點>.to`。
  - 超出 level 或 level 不存在，開頭多一層：`<傳送點>.from.at`、`.size`、`.level`。
- 端點可以蓋在區域或路徑的格上（門開在牆或地板上），只管 entity 的格，不改地形層。

## 改慣例 entity 的顏色與 tags

- `[entity_types]` 用 `PlayerSpawn`、`Teleport`、`Part` 的名字寫 `color`、`tags`，就是覆寫它們的外觀。
- 只能寫這兩項，至少寫一個；名稱、大小與欄位不能改，名稱分大小寫。
- `tags` 是整個取代，不是加上去；`PlayerSpawn` 的 tags 要有 `player`。

```toml
[entity_types]
Teleport = { color = "#30e1b9", tags = ["tele"] }
```

## 固定存成單檔

- 頂層寫 `split = false`：生成時一律存成單檔，不看磁碟上原本怎麼存；`split = true` 一律拆開。
- 沒寫就沿用磁碟上的存法，新建預設拆開（D-002）。
- 例：遊戲的測試要求關卡都在主檔裡，寫了 `split = false`，有人刪掉主檔再生成也不會變成拆開存。
- 存法改變時，舊的 `.ldtkl` 與子資料夾一起收掉；生成成功之後，磁碟上只有一種存法。

## 在 LDtk 裡改過會被擋

- 規則生成的場景地圖，entity 的像素位置與大小都記在生成紀錄裡：在 LDtk 裡挪 1 像素、拉大，重新生成都算直接改過，會被擋下（D-008）。
- 要保留改動，就把它寫進規則檔的 `offset` 或 `size`。
