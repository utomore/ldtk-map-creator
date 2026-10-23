---
status: accepted
date: 2026-10-23
decision-makers: [utomore]
---
# P-004 檢查與合併的關卡

## 當前情境

- P-003 的規則主要會被 LLM 違反（agent 自己寫、自己合）；機器判得了的要自動擋。
- LDtk 沒有 headless 模式，「LDtk 打得開」自動化不了。
- 本地 pre-commit 要在 2 秒內。

## 決策

| 規則 | 由什麼守 | 紅了 |
|---|---|---|
| 風格、格式 | ruff check、ruff format（pre-commit 與 CI） | 擋 |
| 型別正確 | pyright strict（CI） | 擋 |
| law 成立、寫出的檔照 schema、`uid` 不重複 | pytest；寫出的檔用官方 `JSON_SCHEMA.json` 驗證（CI） | 擋 |
| `mcp/`、`cli/` 不寫邏輯 | agent 自審（`/code-review`） | 本次引入的未修就不合併 |
| `core/` 不 import 入口 | agent 自審 | 同上 |
| 測試要對到 law | agent 自審 | 同上 |
| LDtk 1.5.3 實際打得開 | 我或 agent 手動，寫進驗收條件 | 不合併 |
| ①②③⑤⑥ 要我合併 | agent 紀律，PR 標題加 `[需人工合併]` | 不自己合 |

**CI：**

- GitHub Actions，PR 與 push 到 `main` 時跑：ruff、pyright、pytest。
- 和本地的 `make check`、`make test` 是同一道。
- CI 全綠才能合進 `main`；分支保護由我在 GitHub 設定。

**CD：**現在只有 CI；工具在本機跑，沒有部署目標，M4 之後再定。

## 三個月後的影響

- 風格、型別、law 在合併前就擋得下；依賴方向與入口邏輯還是靠自審。
- 我不在時，碰到 ①②③⑤⑥ 的 PR 會卡住，其餘照常合併。
