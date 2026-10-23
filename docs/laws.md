# Law

這個專案必須成立的性質（P-003）。

- 編號 `<前綴>-<n>`，不重用；測試用 `law("<編號>")` 標記。
- 一條 law 一句話：在什麼範圍內、什麼一定成立。

## GEN：生成

## FILE：讀寫檔

- **FILE-1**：repo 裡的範本，以及工具寫出的每個場景地圖檔，都通過官方 LDtk 1.5.3 JSON schema；`.ldtkl` 去掉 LDtk 自己加的 `__header__` 後，以 schema 的 `Level` 驗證。
- **FILE-2**：寫出再讀回的場景地圖裡，圖層定義、entity 定義、level 的 `uid` 互不重複，而且都小於 `nextUid`；所有 `iid` 互不重複。
- **FILE-3**：讀進來、寫回去、再讀一次，內容跟寫之前完全相同，包括範本裡程式沒宣告的欄位。
- **FILE-4**：寫出的每個 IntGrid 圖層，`__cWid` 等於 `ceil(pxWid / gridSize)`、`__cHei` 同理，`intGridCsv` 長度等於 `__cWid × __cHei`。
- **FILE-5**：拆開存時，主檔每個 level 的 `layerInstances` 是 `null`、`externalRelPath` 指向存在的 `.ldtkl`；子資料夾裡沒有對不上 level 的 `.ldtkl`；單檔存時，子資料夾裡沒有任何 `.ldtkl`。
- **FILE-6**：寫出的 `__neighbours` 由 level 的世界座標決定，而且對稱：A 把 B 列為某個方向，B 就把 A 列為反方向（n↔s、w↔e、nw↔se、ne↔sw、o↔o、<↔>）；不相接的 level 不列；線性佈局（LinearHorizontal、LinearVertical）一律不列，照 LDtk 1.5.3 的 `Level.getNeighboursJson`。

## SCENE：場景地圖

## RENDER：預覽圖
