---
status: accepted
date: 2026-10-23
decision-makers: [utomore]
---
# D-004 GitHub Issues 管工作項目，GitHub Actions 跑 CI，ruff 加 pyright strict 加 pytest

## 當前情境

- 一個人加 AI agent；repo 已在 GitHub（`utomore/ldtk-map-creator`），`gh` CLI 可以操作。
- 沒有預算限制；GitHub 不允許自己批准自己的 PR。

## 選擇

| 專案管理 | 適合 | 代價 |
|---|---|---|
| GitHub Issues | 一個人、repo 在 GitHub；issue、PR、里程碑、標籤在同一處 | 看板功能陽春 |
| Trello | 習慣看板 | 卡片和 PR 要手動對連結 |
| GitHub Issues 加 Projects 看板 | 想要看板視圖 | 多維護一個 Project 的欄位與狀態 |

| pre-commit | 適合 | 代價 |
|---|---|---|
| 跑 ruff check 加 ruff format | commit 時就擋風格錯誤，不到 2 秒 | 要裝 pre-commit |
| 不裝，只靠 CI | 最輕 | 錯誤等 CI 才發現 |

| 型別檢查 | 適合 | 代價 |
|---|---|---|
| pyright strict | 從空白開始最便宜 | 寫型別多一點手續 |
| pyright basic | 寫得快 | 之後再開 strict 要補一大堆 |

## 決策

| 項目 | 用什麼 |
|---|---|
| 專案管理 | GitHub Issues（里程碑、標籤；標籤由 `lawfulv2:labels` 建） |
| 格式與 lint | ruff（check 加 format） |
| 型別檢查 | pyright strict |
| 測試 | pytest；寫出的檔用官方 `JSON_SCHEMA.json`（LDtk 1.5.3）驗證 |
| render | Pillow |
| MCP SDK | 官方 `mcp`（見 D-005） |
| pre-commit | ruff check 加 ruff format |
| AI review | agent 合併前跑 `/code-review` |
| CI | GitHub Actions |
| CD | 現在沒有 |

## 三個月後的影響

- 進度在 GitHub 的 milestone 頁面看；換工具要搬 issue。
- 「要人工合併」做不成分支保護，只能靠 agent 紀律與 PR 標題標記（P-004）。
