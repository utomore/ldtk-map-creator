# ldtk-map-creator

讓我和 AI agent 透過對話，從規則生成並微調 LDtk 場景地圖，每一步都看得到預覽圖。

- 給我自己的遊戲用：我和 Claude Code 對話，透過這個工具的 MCP server，從規則生成整張 LDtk 場景地圖，再一步步微調。
- 地圖照固定的分層慣例寫出（IntGrid 區域與 Entity 位置），引擎照慣例就能長出關卡；每一步都輸出 PNG 預覽。
- 現在做到：M4 分層慣例。
  - 從規則檔生成多個 level 的場景地圖：地面、牆、高度、斜坡、傳送點、零件，每步回傳預覽圖與世界總覽。
  - 照 WeftEngine 讀的分層慣例寫出（D-011），`check_scene` 檢查照不照慣例。
  - 規則檔怎麼畫地形見 `docs/guide/terrain.md`；怎麼指定 entity 與傳送點的位置與大小、改慣例 entity 的外觀、固定存法見 `docs/guide/placement.md`。
- 不做：服務其他 LDtk 使用者、引擎端的匯入程式、其他地圖格式、遊戲執行時即時生成。

## 開始

- 安裝：`uv sync`。
- 在 Claude Code 用：這個 repo 的 `.mcp.json` 已經設好 `ldtk-map-creator` MCP server，開啟專案時核准它。
- 在命令列用：`uv run ldtk-map-creator --help`，每個 MCP 工具都有同名的子指令。
- 改完要跑：`bash scripts/check.sh`、`bash scripts/test.sh`。

## 文件

| 在哪 | 是什麼 |
|---|---|
| `docs/adr/` | 決策：技術的抉擇在 `design/`，開發流程與架構規範在 `process/` |
| `docs/glossary.md` | 名詞表：這個專案的用詞與定義 |
| `docs/laws.md` | law：這個專案必須成立的性質 |
| `docs/guide/` | 給人看的說明 |
| `docs/backlog.md` | 還沒定的點子 |
| `CLAUDE.md` | 給 LLM 的專案記憶：每次寫程式都要守的規則 |

工作項目、里程碑與進度在 D-004 定的專案管理工具裡。
