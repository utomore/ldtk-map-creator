---
status: accepted
date: 2026-10-23
decision-makers: [utomore]
---
# D-005 MCP 用官方 `mcp` SDK 2.x 的 MCPServer

## 當前情境

- MCP 層要薄，只把工具呼叫轉給 `core/`（D-003）。
- 要能回傳 PNG 給 Claude Code 看；只在本機用 stdio（D-001）。
- 官方 SDK 已到 2.x：1.x 的 FastMCP 改名為 `MCPServer`，`mcp.server.fastmcp` 已移除。

## 選擇

| 選項 | 適合 | 代價 |
|---|---|---|
| 官方 `mcp` 2.x 的 MCPServer（`from mcp.server.mcpserver import MCPServer, Image`） | 只要基本工具與圖片回傳；跟著官方主線 | 網路範例多半還是 1.x 寫法，要對照官方 migration 指南 |
| 官方 `mcp` 鎖在 1.x 的 FastMCP | 照 1.x 範例寫 | 1.x 遲早停止維護，到時還是要遷移 |
| 獨立套件 `fastmcp`（社群維護） | 要進階功能或 HTTP 遠端部署 | 多一個第三方依賴，API 變動比官方快 |

## 決策

- 用官方 `mcp` 2.x 的 `MCPServer`；`pyproject.toml` 鎖 `mcp>=2,<3`。

## 三個月後的影響

- 官方出 3.x 時要照 migration 指南升級，只改 `mcp/` 一層。
- 要換成其他 SDK，同樣只改 `mcp/` 一層，代價小。
