# 用規則檔畫地形

寫規則檔時，地面、牆、高度與斜坡怎麼寫（D-011、D-012）。

## 寫法

```toml
[grounds]                     # Ground 層的值：遊戲對到材質
grass = { value = 1, color = "#7CB342" }
dirt  = { value = 2, color = "#C8B27A" }

[collisions]                  # Collision 層的值：不是 0 就是實心
wall  = { value = 1, color = "#000000" }
water = { value = 2, color = "#2F4CBE" }

[kinds]                       # 元素畫成什麼：寫進哪幾層
plaza = { ground = "dirt" }
house = { ground = "dirt", collision = "wall" }
hill  = { ground = "grass", height = 2 }
ramp  = { ground = "grass", height = 1, slope = true }
pond  = { collision = "water" }
```

- 圖格種類至少要寫進一層；`height` 是 0 到 64 的階數。
- 舊寫法 `plaza = { value = 1, color = "..." }` 會報錯。

## 斜坡

- 斜坡的每一格，北東南西至少一格要剛好高一階；引擎朝那一格長出斜面。
- 不合格時生成會擋下，例如 `ramp.kind：斜坡格 (6,19) 四周沒有高一階的格（D-012）`。
- 斜坡只能一格深：兩格深的坡道，第二排碰不到高處。
  - 要更長的坡，一階一階疊：`height = 1` 的斜坡貼著 `height = 2` 的平台，再往外接 `height = 0` 的斜坡……
- 寬的坡道用 `at` 固定位置，不要用 `near`：
  - `near` 只保證兩塊相鄰，沿著那一邊的位置是亂數，坡道可能錯開一截，只有一部分貼著高處。
  - 範例：`tests/data/village_terrain.rules.toml` 的 `hill` 與 `ramp`。

```toml
[[area]]
name = "hill"
kind = "hill"
size = [7, 5]
at = [6, 13]

[[area]]
name = "ramp"
kind = "ramp"
size = [3, 1]      # 一格深，三格都貼著小丘的南邊
at = [6, 18]
```

## 確認

- 生成之後跑 `check_scene`（MCP 工具或 `uv run ldtk-map-creator check_scene <場景地圖.ldtk>`），回「照分層慣例，沒有問題」才算數。
