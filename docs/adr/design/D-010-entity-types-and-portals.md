---
status: accepted
date: 2026-10-23
decision-makers: [utomore]
---
# D-010 entity 類型由規則檔宣告，傳送點是一種元素

## 當前情境

- M3 要傳送點跨 level 連接；傳送點是帶 EntityRef 欄位的 entity（#16）。
- 範本只有一個 entity 定義 `Spawn`；IntGrid 的值由規則檔的 `[kinds]` 定（D-007）。
- 情境：house_1 門口一個傳送點，連到 `House1` 裡的出口；兩邊各一個 entity，`target` 欄位互指。

## 選擇

| 選項 | 適合 | 代價 |
|---|---|---|
| 規則檔宣告 entity 類型（跟 `[kinds]` 一樣），傳送點是新的一種元素 | 顏色、大小、欄位都在規則裡調；不提早定慣例 | 跟 `[kinds]` 一樣跟 M4 的分層慣例重疊 |
| 範本固定加一個 `Portal` 定義 | 最省事 | 範本提早變成事實上的慣例；改名或加欄位就是改分層慣例 |
| 第一次用到就自動建定義 | 寫得最少 | 顏色、大小、欄位都沒地方調；慣例藏在程式碼裡 |

## 決策

- 規則檔用 `[entity_types]` 宣告 entity 類型：顏色、大小、欄位（欄位型別至少 Int、Float、Bool、String、EntityRef）、tags（字串陣列，沒寫是空的；生成時寫進 entity 定義的 `tags`，實例的 `__tags` 跟著有）。
- 分層慣例的 entity（`PlayerSpawn`、`Teleport`、`Part`）由 D-011 固定，`[entity_types]` 宣告遊戲自己的；用慣例 entity 的名字寫的，只是覆寫它的 `color`、`tags`，大小與欄位不變（D-014）。
- 傳送點是新的一種元素 `[[portal]]`：`from`、`to` 是兩個端點，可以在不同 level；端點是錨點，或固定的位置與大小（D-014）。
- 生成時兩邊各擺一個 `Teleport`，`targetPort` 欄位互指（D-011）；iid 由 seed 加元素名稱推出，重新生成引用不斷（D-007）。
- 區域加上四邊的錨點 `.north`、`.east`、`.south`、`.west`：在區域自己的格裡、那一邊的正中央。

## 三個月後的影響

- 傳送點的類型名稱與欄位名稱照 D-011，引擎直接讀得到。
