---
status: accepted
date: 2026-10-23
decision-makers: [utomore]
---
# D-009 規則檔用 `[[level]]` 陣列描述多個 level 與世界佈局

## 當前情境

- M3 要一個場景地圖有多個 level 加世界佈局（P-002）。
- D-007 的規則檔只有一個 `[level]`；D-002 定了一個場景地圖一份規則檔。
- 情境：村莊 `Village`（40x30）東邊隔 2 格放一間房子的內部 `House1`（12x10）。

## 選擇

| 選項 | 適合 | 代價 |
|---|---|---|
| `[[level]]` 陣列，元素加 `level` 欄位 | M2 的規則檔照樣能用；名稱整個場景唯一，跨 level 用名字就指得到 | level 一多，檔案很長 |
| 每個 level 一段巢狀表 | 一個 level 的東西寫在一起 | 名稱只在 level 內唯一，跨 level 要寫 `House1:room`；M2 的格式要改 |
| 每個 level 一份規則檔，加一份世界檔 | 很大的世界 | 要改 D-002；跨檔引用與 seed 要另外設計 |

## 決策

- 規則檔用 `[[level]]` 陣列，每個 level 有 `name`、`size`。
- level 在世界上的位置跟區域一樣寫：`at = [x, y]`（世界座標，格），或 `near`、`side`、`gap` 靠近另一個 level；第一個 level 沒寫就在 (0, 0)。
- 元素加 `level` 欄位，指它在哪個 level；只有一個 level 時可以省略。
- 元素名稱整個場景唯一；M2 的 `[level]` 寫法照樣能讀。
- 世界佈局用 LDtk 的 Free，level 彼此不重疊。

## 三個月後的影響

- level 一多，規則檔會很長；真的太長時再考慮拆檔（選項三），到時要改 D-002。
- level 的擺法跟區域共用 near / side / gap 的語意，配置器的規則要同時適用兩者。
