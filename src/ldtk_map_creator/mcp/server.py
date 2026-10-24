"""MCP 入口：每個操作一個工具，只轉呼叫 core.actions（P-003、D-005）。

會改動場景地圖的工具，回傳摘要文字加上更新後的預覽圖。
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ..core import actions
from ..core.actions import ActionResult
from ..core.errors import SceneError

server = MCPServer(
    "ldtk-map-creator",
    instructions=(
        "生成並微調 LDtk 場景地圖。path 是場景地圖的 .ldtk 主檔路徑；座標以格為單位，"
        "原點在 level 左上角。先用 describe_scene 看有哪些 level、圖層、IntGrid 值與 entity。"
        "圖層照分層慣例（D-011）：Entities、Collision、Slope、Height、Ground；"
        "畫格要用 layer 指定層。"
        "會改動地圖的工具都會回傳預覽圖。"
        "有 <名>.rules.toml 的場景地圖是從規則生成的：微調改規則檔，再呼叫 generate_scene。"
    ),
)

Reply = list[str | Image]


def _reply(run: Callable[[], ActionResult]) -> Reply:
    """執行操作；SceneError 轉成 ToolError，agent 才看得到錯在哪。"""
    try:
        result = run()
    except SceneError as error:
        raise ToolError(str(error)) from error
    return [result.summary, *(Image(path=p) for p in result.previews)]


@server.tool()
def create_scene(path: str, split: bool = True) -> Reply:
    """建一個新的場景地圖（含空的 Level_0）。split=True 時每個 level 存成獨立的 .ldtkl。"""
    return _reply(lambda: actions.create_scene(Path(path), split=split))


@server.tool()
def describe_scene(path: str) -> Reply:
    """列出場景地圖的 level、圖層、IntGrid 值、entity 定義與每個 entity 的位置與 iid。"""
    return _reply(lambda: actions.describe_scene(Path(path)))


@server.tool()
def add_level(
    path: str,
    identifier: str,
    width_cells: int,
    height_cells: int,
    world_x: int = 0,
    world_y: int = 0,
) -> Reply:
    """新增一個空的 level；尺寸以格為單位，世界座標以像素為單位。"""
    return _reply(
        lambda: actions.add_level(
            Path(path),
            identifier,
            width_cells=width_cells,
            height_cells=height_cells,
            world_x=world_x,
            world_y=world_y,
        )
    )


@server.tool()
def paint_rect(
    path: str,
    level: str,
    x: int,
    y: int,
    value: int,
    width: int = 1,
    height: int = 1,
    layer: str | None = None,
) -> Reply:
    """把矩形範圍內的 IntGrid 格設成 value（0 是清除）。

    場景地圖有四個 IntGrid 層（Collision、Slope、Height、Ground，D-011），要用 layer 指定畫哪一層。
    """
    return _reply(
        lambda: actions.paint_rect(
            Path(path), level, x=x, y=y, width=width, height=height, value=value, layer=layer
        )
    )


@server.tool()
def paint_cells(
    path: str, level: str, cells: list[tuple[int, int]], value: int, layer: str | None = None
) -> Reply:
    """把指定的幾格 [[x, y], ...] 設成 value（0 是清除）；任何一格出界就全部不改。

    分層慣例有四個 IntGrid 層（Collision、Slope、Height、Ground），要用 layer 指定畫哪一層。
    """
    return _reply(
        lambda: actions.paint_cells(Path(path), level, cells=cells, value=value, layer=layer)
    )


@server.tool()
def place_entity(
    path: str,
    level: str,
    entity: str,
    x: int,
    y: int,
    layer: str | None = None,
    values: dict[str, Any] | None = None,
) -> Reply:
    """在 (x, y) 格擺一個 entity（entity 是定義的名稱，例如 PlayerSpawn）；values 是欄位值。"""
    return _reply(
        lambda: actions.place_entity(
            Path(path), level, entity, x=x, y=y, layer=layer, values=values
        )
    )


@server.tool()
def move_entity(path: str, iid: str, x: int, y: int) -> Reply:
    """把 entity（用 iid 指定）移到同一個 level 的 (x, y) 格。"""
    return _reply(lambda: actions.move_entity(Path(path), iid, x=x, y=y))


@server.tool()
def delete_entity(path: str, iid: str) -> Reply:
    """刪掉一個 entity（用 iid 指定）。"""
    return _reply(lambda: actions.delete_entity(Path(path), iid))


@server.tool()
def render_level(path: str, level: str, scale: int = 2) -> Reply:
    """重新 render 一個 level 的預覽圖，不改動場景地圖。"""
    return _reply(lambda: actions.render_level(Path(path), level, scale=scale))


@server.tool()
def generate_scene(path: str, force: bool = False) -> Reply:
    """照場景地圖旁的 <名>.rules.toml 生成場景地圖；已經有地圖時重新生成。

    微調一律改規則檔再呼叫這個工具（D-008）。地圖在上次生成之後被直接改過時會拒絕並列出差異；
    force=True 才蓋掉那些改動。
    """
    return _reply(lambda: actions.generate_scene(Path(path), force=force))


@server.tool()
def add_entity_def(
    path: str,
    identifier: str,
    color: str = "#BE4A2F",
    width: int = 16,
    height: int = 16,
    field_types: dict[str, str] | None = None,
) -> Reply:
    """新增一個 entity 定義。

    field_types 是欄位名稱 → 型別（Int、Float、Bool、String、EntityRef）。
    有 <名>.rules.toml 的場景地圖不要用這個：改規則檔再 generate_scene（D-008）。
    """
    return _reply(
        lambda: actions.add_entity_def(
            Path(path),
            identifier,
            color=color,
            width=width,
            height=height,
            field_types=field_types,
        )
    )


@server.tool()
def set_entity_fields(path: str, iid: str, values: dict[str, Any]) -> Reply:
    """設定 entity 的欄位值（欄位名稱 → 值；null 是清空）。EntityRef 的值是目標 entity 的 iid。

    有 <名>.rules.toml 的場景地圖不要用這個：改規則檔再 generate_scene（D-008）。
    """
    return _reply(lambda: actions.set_entity_fields(Path(path), iid, values))


def main() -> None:
    server.run("stdio")
