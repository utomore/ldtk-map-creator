---
status: accepted
date: 2026-10-23
decision-makers: [utomore]
---
# P-003 架構規範

## 當前情境

- D-003 選了 `core/`、`mcp/`、`cli/` 三塊；型錄沒有這一格，規則只記決策、不附檢查。
- 不守的話，會先壞在邏輯散到 MCP 工具裡，CLI 與 MCP 的行為開始不一樣。

## 決策

```
src/<套件名>/
  core/   場景地圖模型、IntGrid / Entity 操作、規則生成、render、讀寫檔
  mcp/    MCPServer 工具，只轉呼叫 core
  cli/    CLI 指令，只轉呼叫 core
依賴方向：mcp → core ← cli
```

- `core/` 放所有規則；不 import `mcp/` 與 `cli/`。
- `mcp/`、`cli/` 只做參數轉換、呼叫 `core/`、把結果或錯誤轉成入口的格式；不寫邏輯。
- 讀寫檔與 render 的 I/O 也在 `core/`。
- 命名只用 `docs/glossary.md` 登記過的名詞。

**law 住在哪裡：**

- 整個專案一份 `docs/laws.md`，用前綴分區：`GEN-`（生成）、`FILE-`（讀寫檔）、`SCENE-`（場景地圖）、`RENDER-`（預覽圖）。
- 編號如 `GEN-1`，不重用。
- 測試用 `law("<編號>")` 標記對到 law，例如 `GEN-1` 對 `test_gen_1_same_seed_same_output`。

## 三個月後的影響

- 一份 law 檔一眼看完；檔變長要靠前綴分區。
- 依賴方向與「入口不寫邏輯」沒有機器擋，靠 agent 自審。
