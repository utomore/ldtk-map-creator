---
status: accepted
date: 2026-10-23
decision-makers: [utomore]
---
# D-005 MCP 用官方 `mcp` SDK 內建的 FastMCP

## 當前情境

- MCP 層要薄，只把工具呼叫轉給 `core/`（D-003）。
- 要能回傳 PNG 給 Claude Code 看；只在本機用 stdio（D-001）。

## 選擇

| 選項 | 適合 | 代價 |
|---|---|---|
| 官方 `mcp` SDK 的 FastMCP（`from mcp.server.fastmcp import FastMCP, Image`） | 只要基本工具與圖片回傳 | 進階功能較少（工具組合、伺服器代理） |
| 獨立套件 `fastmcp`（2.x，社群維護） | 要進階功能或 HTTP 遠端部署 | 多一個第三方依賴，API 變動比官方快 |

## 決策

- 用官方 `mcp` SDK 內建的 FastMCP。

## 三個月後的影響

- 兩者寫法接近，要換成 `fastmcp` 只改 `mcp/` 一層，代價小。
