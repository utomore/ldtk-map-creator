"""ENTRY-1：操作、MCP 工具、CLI 指令一一對應；入口的典型用法。"""

import asyncio
import inspect
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from ldtk_map_creator.cli.main import COMMANDS, main
from ldtk_map_creator.core import actions
from ldtk_map_creator.mcp.server import server


@pytest.mark.law("ENTRY-1")
def test_entry_1_every_action_has_one_tool_and_one_command() -> None:
    tools = {tool.name for tool in asyncio.run(server.list_tools())}
    # 這個模組自己定義的公開函式，就是全部的操作
    public = {
        name
        for name, value in vars(actions).items()
        if inspect.isfunction(value)
        and value.__module__ == actions.__name__
        and not name.startswith("_")
    }
    assert set(actions.ACTIONS) == public == tools == set(COMMANDS)
    assert len(actions.ACTIONS) == len(set(actions.ACTIONS))


def test_example_mcp_round_trip(tmp_path: Path) -> None:
    """典型：透過 MCP 建場景地圖、畫格、擺 entity，每步回傳摘要與預覽圖；錯誤訊息傳得回去。"""
    path = str(tmp_path / "village.ldtk")

    async def go() -> None:
        steps = [
            ("create_scene", {"path": path}),
            (
                "paint_rect",
                {
                    "path": path,
                    "level": "Level_0",
                    "x": 0,
                    "y": 0,
                    "width": 16,
                    "value": 1,
                    "layer": "Collision",
                },
            ),
            (
                "place_entity",
                {"path": path, "level": "Level_0", "entity": "PlayerSpawn", "x": 3, "y": 3},
            ),
        ]
        for name, arguments in steps:
            result = await server.call_tool(name, arguments)
            assert isinstance(result, CallToolResult)
            kinds = [c.type for c in result.content]
            assert kinds == ["text", "image"], name
        with pytest.raises(ToolError, match="沒有宣告值 9"):
            await server.call_tool(
                "paint_rect",
                {
                    "path": path,
                    "level": "Level_0",
                    "x": 0,
                    "y": 0,
                    "value": 9,
                    "layer": "Collision",
                },
            )

    asyncio.run(go())
    assert (tmp_path / "previews" / "village" / "Level_0.png").is_file()


def test_example_cli_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """典型：CLI 做同樣的事；錯誤回傳 1 並把訊息寫到 stderr。"""
    path = str(tmp_path / "village.ldtk")
    assert main(["create_scene", path]) == 0
    assert main(["paint_cells", path, "Level_0", "1", "0,0", "1,0", "--layer", "Collision"]) == 0
    assert main(["describe_scene", path]) == 0
    assert "Level_0：16x16 格" in capsys.readouterr().out
    assert main(["place_entity", path, "Level_0", "Nope", "0", "0"]) == 2
    assert "沒有叫 Nope 的 entity 定義" in capsys.readouterr().err
