# ADR

專案的決定，一份一個決定，分兩類，各住一個資料夾：

- **設計**（`design/`，編號 `D-<三位數>`）：技術的抉擇。從幾個選項裡挑一個，所以一定有「選擇」一節。
- **流程**（`process/`，編號 `P-<三位數>`）：開發流程、架構規範這類不是技術選題的規則。

格式是精簡的 MADR：frontmatter 三個欄位，內文四節（流程沒有「選擇」）。

```markdown
---
status: accepted            # proposed / accepted / superseded by D-<三位數>
date: YYYY-MM-DD
decision-makers: [帳號]
---
# D-<三位數> 決定的一句話

## 當前情境
## 選擇
## 決策
## 三個月後的影響
```

檔名 `<編號>-<英文短名>.md`，編號不重用。

## 什麼才寫成一份

**設計**：有考慮過別的選項，或者反悔要付代價，才寫成一份。沒有選擇空間的工具選擇（例如 formatter 用哪個）沒人會反對、三個月後也沒影響，寫成一份只是雜訊：併進 D-004 的工具表，一列一個。「選擇」一節列不出第二個真的考慮過的選項、也說不出反悔的代價，就是這種。只考慮過一個選項而反悔要付代價的（例如語言），照樣寫成一份，「選擇」寫它與反悔的代價。

**流程**：每次寫程式、開工作項目、審查或合併都要守的規則。

## 設計

| 編號 | 決定 |
|---|---|
| [D-001](design/D-001-runtime.md) | 語言與執行環境：語言與版本、套件管理、在哪裡執行與部署 |
| [D-002](design/D-002-storage.md) | 資料放哪裡 |
| [D-003](design/D-003-architecture.md) | 軟體架構：採用哪一種架構 |
| [D-004](design/D-004-tools.md) | 工具：專案管理、檢查、CI 與 CD 用什麼 |
| [D-005](design/D-005-mcp-sdk.md) | MCP 用官方 `mcp` SDK 2.x 的 MCPServer |
| [D-006](design/D-006-ldtk-model.md) | LDtk 檔用「空專案範本加精簡自寫型別」表示 |
| [D-007](design/D-007-rules-file.md) | 規則檔用宣告式：地圖上每一塊東西寫成一個有名字的元素 |
| [D-008](design/D-008-tweak-via-rules.md) | 從規則生成的場景地圖，微調一律改規則檔再重新生成 |
| [D-009](design/D-009-multi-level-rules.md) | 規則檔用 `[[level]]` 陣列描述多個 level 與世界佈局 |
| [D-010](design/D-010-entity-types-and-portals.md) | entity 類型由規則檔宣告，傳送點是一種元素 |
| [D-011](design/D-011-layer-convention.md) | 分層慣例照 WeftEngine 讀的名稱；慣例是下限 |
| [D-012](design/D-012-generate-guarantees-convention.md) | 生成保證照分層慣例：斜坡不合格就擋下 |
| [D-013](design/D-013-kind-parts.md) | 圖格種類可以帶零件：生成時在區域中心擺 Part |
| [D-014](design/D-014-entity-placement.md) | entity 與傳送點可以指定位置與大小，慣例 entity 可以改外觀，存法可以由規則檔指定 |

## 流程

| 編號 | 決定 |
|---|---|
| [P-001](process/P-001-north-star.md) | 北極星與不做清單 |
| [P-002](process/P-002-workflow.md) | 工作流程：工作項目、里程碑、種類的待遇、第二把尺、分支、審查與合併、報告 |
| [P-003](process/P-003-architecture-rules.md) | 架構規範：程式碼怎麼切、依賴方向、law 住在哪裡 |
| [P-004](process/P-004-gates.md) | 檢查與合併的關卡：每條規則由什麼守、哪幾步綠了才能合、誰放行 |

## 待決定

方向已定、細節還要討論的，定案時各寫成一份。

- CD：工具在本機跑、沒有部署目標，M4 之後再定（P-004）。

上線之前是第一版的建置期：決定改了就直接改寫那一份，「當前情境」只寫這個案子的需求與限制。
上線之後決定改了才寫新的一份，舊的 `status` 改成 `superseded by <新的編號>`，不改寫舊的內容。
