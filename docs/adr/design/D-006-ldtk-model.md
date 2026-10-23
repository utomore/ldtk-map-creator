---
status: accepted
date: 2026-10-23
decision-makers: [utomore]
---
# D-006 LDtk 檔用「LDtk 存的空專案當範本，加精簡的自寫型別」表示

## 當前情境

- `.ldtk` 的必填欄位多（根層近 30 個、每個圖層實例 20 個左右）；`__` 開頭是衍生欄位，`uid`、`iid` 要一致。
- 官方 quicktype 型別 `LdtkJson.py` 給讀用，不保證寫回不掉資料；沒有成熟的 Python 寫檔套件。
- 這個工具只動 IntGrid 與 Entity（P-001），用不到整份 schema。

## 選擇

| 選項 | 適合 | 代價 |
|---|---|---|
| 官方 quicktype 完整型別 | 百分之百對齊官方 schema | 141K 自動產生、難讀；寫回可能掉欄位；升版要重新產生 |
| 範本加精簡自寫型別 | 只動 IntGrid 與 Entity、要從簡 | 要自己確認衍生欄位算對，靠測試與用 LDtk 打開驗證 |
| 直接操作 dict | 最快開始 | pyright 抓不到錯，欄位拼錯要到 LDtk 打開才發現 |

## 決策

- 用 LDtk 1.5.3 存的空專案當範本；程式只為會動到的部分寫精簡型別。
- `__` 衍生欄位與 `iid`、`uid`、`nextUid` 由同一個函式統一補。
- 沒動到的欄位照範本原樣保留。

## 三個月後的影響

- 要支援圖塊美術（backlog）時，型別要擴到 tile 與 tileset。
- 還沒實測：LDtk 開檔會不會重算 auto-layer、會不會修正缺漏欄位；M1 要用 LDtk 1.5.3 驗。
