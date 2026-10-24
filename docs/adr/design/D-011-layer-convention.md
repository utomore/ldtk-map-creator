---
status: accepted
date: 2026-10-24
decision-makers: [utomore]
---
# D-011 分層慣例照 WeftEngine 讀的名稱

## 當前情境

- M4 要「我的遊戲照慣例讀得進一份生成的村莊」；遊戲用 WeftEngine，引擎已經照固定的名稱讀 LDtk（#46）。
- 這個工具原本生成的層名 `IntGrid`、entity `Spawn`、`Portal`（`target`），引擎都不認得。
- 情境：村莊的廣場鋪泥土；`house_1` 是實心的房子；一座兩階的小丘，北邊一段斜坡；一個出生點；房門的傳送點連到 `House1`；廣場邊一棵橡樹。

## 選擇

| 選項 | 適合 | 代價 |
|---|---|---|
| 照引擎現有的名稱（選中） | 引擎直接讀，不用轉換 | 規則檔的圖格種類要改成分層寫；引擎改名時兩邊一起改 |
| 每張地圖在規則檔自己宣告層與意義 | 不綁引擎 | 慣例變成紀律；檢查查不了意義 |
| 工具用自己的名稱，改引擎去讀 | 這邊幾乎不動 | 改別的 repo 的核心；兩套名字並存 |

## 決策

- 慣例是下限：下面列的名稱必須存在、意義固定；其他 LDtk 功能（多的層、欄位、tags、level 欄位）都可以用，檢查不擋，引擎不認得的就略過。
- 圖層至少這五層，LDtk 裡由上到下：

| 層 | 類型 | 意義 |
|---|---|---|
| `Entities` | Entities | 下面三種 entity，加上遊戲自己的 |
| `Collision` | IntGrid | 不是 0 就是實心；可以宣告多個值（例如牆、水），讓遊戲分辨；0 走得過 |
| `Slope` | IntGrid | `1` 是斜坡，朝高一階的鄰格長成斜面 |
| `Height` | IntGrid | 值是階數；0 是地面 |
| `Ground` | IntGrid | 值是地面種類，遊戲對到材質；0 是預設地面 |

- entity 至少這三種，遊戲可以再宣告自己的：

| entity | tags | 欄位 | 意義 |
|---|---|---|---|
| `PlayerSpawn` | `player` | - | 出生點 |
| `Teleport` | - | `targetPort`（EntityRef） | 傳送點，指到對面的 `Teleport` |
| `Part` | - | `part`（String）、`yaw`（Float，預設 0） | 擺一個零件 |

- 規則檔：
  - `[grounds]` 宣告地面種類、`[collisions]` 宣告實心的種類：各自的值與預覽顏色。
  - `[kinds]` 的圖格種類改成「寫進哪幾層」：`ground`、`collision`（填上面宣告的名稱）、`height`、`slope`。
  - `[[portal]]` 一律生成 `Teleport`；`[[entity]]` 可以帶欄位值，例如 `part = "oak"`。
  - `[entity_types]` 只宣告遊戲自己的 entity，不能用上面三個名稱。

```toml
[grounds]
grass = { value = 1, color = "#7CB342" }
dirt  = { value = 2, color = "#C8B27A" }

[collisions]
wall  = { value = 1, color = "#000000" }
water = { value = 2, color = "#2F4CBE" }

[kinds]
plaza = { ground = "dirt" }
house = { ground = "dirt", collision = "wall" }
hill  = { ground = "grass", height = 2 }
ramp  = { ground = "grass", height = 1, slope = true }

[[entity]]
name = "oak_1"
type = "Part"
at = "plaza.east"
fields = { part = "oak", yaw = 90 }
```

- 檢查是一個操作：場景地圖不照慣例就列出每一處，例如缺層、值不合法、斜坡旁沒有高一階的格、`Teleport` 沒指到別的 `Teleport`、`Part` 的 `part` 是空的。
- 新建的空場景地圖（不經規則）也照這五層與三種 entity。

## 三個月後的影響

- 引擎改層名或欄位時，這份與工具一起改；改這份要人工合併（P-002 ②）。
- 2D 平台遊戲用同一個引擎，名稱照用；用不到的層留空。
- 引擎支援新的 LDtk 功能（例如 Enum 欄位、圖塊）時，在這份加一條，工具與檢查跟著補。
- 舊格式的規則檔（`[kinds]` 帶 `value`）讀取時報錯，範例照新格式改寫。
- 第二把尺加上這份（P-002）。
