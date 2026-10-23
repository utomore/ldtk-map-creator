---
status: accepted
date: 2026-10-23
decision-makers: [utomore]
---
# D-001 Python 3.13，用 uv 管套件，在本機以 stdio 跑 MCP server

## 當前情境

- 只有我加 AI agent 開發，沒有期限、不移交。
- 工具在我的 Windows 電腦上跑，由 Claude Code 啟動；手機透過 Remote Control 連回同一個 session 看 PNG。
- 目標的 LDtk 是本機安裝的 1.5.3（官方最新版）。

## 選擇

| 選項 | 適合 | 代價 |
|---|---|---|
| uv Python | 畫 PNG（Pillow）與程序生成最省力；跟這台機器一律用 uv 的慣例一致 | 反悔改 TypeScript 要重寫整個核心 |
| TypeScript | MCP 生態的預設；遊戲是網頁或 TS 引擎時能共用型別 | 畫圖要靠原生依賴，Windows 上偶爾卡住 |
| 本機 stdio | 地圖檔就在這台電腦的遊戲專案裡 | 這台電腦要開著 |
| 另架 HTTP server | 別台機器或網頁也能用 | 要同步地圖檔、處理部署與驗證 |

## 決策

| 項目 | 決定 |
|---|---|
| 語言與版本 | Python 3.13 |
| 套件管理 | uv（執行用 `uv run`，加套件用 `uv add`） |
| 執行環境 | 本機 Windows；MCP server 以 stdio 由 Claude Code 啟動 |
| 部署 | 不部署 |
| 目標 LDtk | 1.5.3 |

## 三個月後的影響

- 要在別台機器或網頁用，要改成 HTTP 啟動；官方 SDK 只要改啟動方式，代價小。
- LDtk 升版時要重新驗證範本與衍生欄位（D-006）。
