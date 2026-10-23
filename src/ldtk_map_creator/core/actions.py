"""操作（glossary：操作）：入口直接呼叫的單位，一個操作對應一個 MCP 工具與一個 CLI 指令。

每個會改動地圖的操作都是一個小交易（SCENE-5）：
1. 從磁碟讀場景地圖，在記憶體裡做完那件事。
2. 找出內容有變的 level，先在記憶體裡 render 好它們的預覽圖。
3. 寫回場景地圖（只寫有變的 level）與預覽圖；中途失敗就把動過的檔還原。
所以 MCP 與 CLI 不用保存狀態，兩邊的行為也一樣（P-003）。
"""

import contextlib
import copy
import functools
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from . import edit, scene
from . import render as rendering
from .derived import cell_count, refresh_derived
from .errors import SceneError
from .files import atomic_write
from .scene import SceneMap

ACTIONS = (
    "create_scene",
    "describe_scene",
    "add_level",
    "paint_rect",
    "paint_cells",
    "place_entity",
    "move_entity",
    "delete_entity",
    "render_level",
)
"""所有操作的名稱；MCP 工具與 CLI 指令跟它一一對應（ENTRY-1）。"""


@dataclass(frozen=True)
class ActionResult:
    """操作的結果：給人看的摘要、這次更新的預覽圖，與新擺的 entity 的 iid（有的話）。"""

    summary: str
    previews: tuple[Path, ...] = ()
    iid: str | None = None


# 操作是「讀、改、寫」的交易；MCP 會在多個執行緒同時跑工具，所以全部排隊執行
_LOCK = threading.RLock()


def _serialized[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(fn)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        with _LOCK:
            return fn(*args, **kwargs)

    return run


def _snapshot(paths: set[Path]) -> dict[Path, bytes | None]:
    return {p: p.read_bytes() if p.is_file() else None for p in paths}


def _restore(saved: dict[Path, bytes | None]) -> None:
    """還原真的被改過的檔；某個檔還原失敗也繼續還原其餘的。"""
    for path, data in saved.items():
        with contextlib.suppress(OSError, SceneError):
            current = path.read_bytes() if path.is_file() else None
            if current == data:
                continue
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, data)


def _for_render(level: object) -> object:
    """比較預覽圖會不會變時，不看跟畫面無關的相鄰關係。"""
    if isinstance(level, dict):
        return {k: v for k, v in cast(dict[str, object], level).items() if k != "__neighbours"}
    return level


def _commit(
    target: SceneMap,
    before: dict[str, object] | None,
    summary: str,
    primary: str,
    iid: str | None = None,
) -> ActionResult:
    """寫回有變的 level 與它們的預覽圖；失敗時還原磁碟上動過的檔。

    - `before` 是操作前每個 level（以 iid 為鍵）的複本；None 表示全部都是新的。
    - `primary` 是這次操作的目標 level：就算內容沒變，也一定回傳它的預覽圖。
    """
    refresh_derived(target.project)
    levels = target.project["levels"]
    changed = [lv["identifier"] for lv in levels if before is None or before.get(lv["iid"]) != lv]
    redraw = [
        lv["identifier"]
        for lv in levels
        if before is None
        or _for_render(before.get(lv["iid"])) != _for_render(lv)
        or lv["identifier"] == primary
    ]
    images = {level_id: rendering.render_level(target, level_id) for level_id in redraw}
    folder = target.path.parent / target.path.stem
    names = {lv["identifier"] for lv in levels}
    stale = {p for p in folder.glob("*.ldtkl") if p.stem not in names}
    touched = {target.path, *stale, *(folder / f"{lv}.ldtkl" for lv in changed)}
    touched |= {rendering.preview_path(target, lv) for lv in redraw}
    saved = _snapshot(touched)
    try:
        scene.save_scene(target, levels=set(changed), refreshed=True)
        previews = tuple(rendering.write_preview(target, lv, image) for lv, image in images.items())
    except BaseException:
        _restore(saved)
        raise
    return ActionResult(summary, previews, iid)


def _apply(path: Path, change: Callable[[SceneMap], tuple[str, str, str | None]]) -> ActionResult:
    """讀場景地圖、做 `change`（回傳摘要、目標 level、新 iid），再交給 _commit 寫回。"""
    target = scene.load_scene(path)
    before: dict[str, object] = {lv["iid"]: copy.deepcopy(lv) for lv in target.project["levels"]}
    summary, primary, iid = change(target)
    return _commit(target, before, summary, primary, iid)


@_serialized
def create_scene(path: Path, *, split: bool = True) -> ActionResult:
    """建一個新的場景地圖，含一個空的 Level_0。"""
    target = scene.new_scene(path, split=split)
    return _commit(
        target, None, f"建好場景地圖 {path}（{'拆開存' if split else '單檔'}）", "Level_0"
    )


@_serialized
def describe_scene(path: Path) -> ActionResult:
    """列出場景地圖的 level、圖層、IntGrid 值、entity 定義與 entity；不改動檔案。"""
    target = scene.load_scene(path)
    refresh_derived(target.project)  # 只在記憶體裡重算，位置不靠檔案裡可能過期的 __grid
    return ActionResult(_describe(target))


def _describe(target: SceneMap) -> str:
    project = target.project
    grid = project["defaultGridSize"]
    lines = [f"場景地圖 {target.path}（格子 {grid}px，{'拆開存' if target.split else '單檔'}）"]
    lines.append("圖層：")
    for ldef in project["defs"]["layers"]:
        values = "、".join(
            f"{v['value']}={v['identifier'] or '（未命名）'}（{v['color']}）"
            for v in ldef["intGridValues"]
        )
        extra = f"，值：{values}" if values else ""
        lines.append(f"  - {ldef['identifier']}（{ldef['type']}，{ldef['gridSize']}px{extra}）")
    lines.append("entity 定義：")
    for edef in project["defs"]["entities"]:
        lines.append(f"  - {edef['identifier']}（{edef['width']}x{edef['height']}px）")
    lines.append("level：")
    for level in project["levels"]:
        cols = cell_count(level["pxWid"], grid)
        rows = cell_count(level["pxHei"], grid)
        lines.append(
            f"  - {level['identifier']}：{cols}x{rows} 格，世界座標 "
            f"({level['worldX']}, {level['worldY']})"
        )
        for layer in level["layerInstances"] or []:
            for entity in layer["entityInstances"]:
                gx, gy = entity["__grid"]
                lines.append(f"      {entity['__identifier']} 在 ({gx}, {gy})，iid {entity['iid']}")
    return "\n".join(lines)


@_serialized
def add_level(
    path: Path,
    identifier: str,
    *,
    width_cells: int,
    height_cells: int,
    world_x: int = 0,
    world_y: int = 0,
) -> ActionResult:
    """新增一個空的 level。"""

    def change(target: SceneMap) -> tuple[str, str, str | None]:
        scene.add_level(
            target,
            identifier,
            width_cells=width_cells,
            height_cells=height_cells,
            world_x=world_x,
            world_y=world_y,
        )
        return f"新增 level {identifier}：{width_cells}x{height_cells} 格", identifier, None

    return _apply(path, change)


@_serialized
def paint_rect(
    path: Path,
    level: str,
    *,
    x: int,
    y: int,
    width: int = 1,
    height: int = 1,
    value: int,
    layer: str | None = None,
) -> ActionResult:
    """把矩形範圍內的格子設成 value（0 是清除）。"""

    def change(target: SceneMap) -> tuple[str, str, str | None]:
        changed = edit.paint_rect(
            target, level, x=x, y=y, width=width, height=height, value=value, layer=layer
        )
        return f"{level}：({x},{y}) {width}x{height} 設成 {value}，改了 {changed} 格", level, None

    return _apply(path, change)


@_serialized
def paint_cells(
    path: Path,
    level: str,
    *,
    cells: list[tuple[int, int]],
    value: int,
    layer: str | None = None,
) -> ActionResult:
    """把指定的幾格設成 value（0 是清除）。"""

    def change(target: SceneMap) -> tuple[str, str, str | None]:
        changed = edit.paint_cells(target, level, cells=cells, value=value, layer=layer)
        return f"{level}：{len(cells)} 格設成 {value}，改了 {changed} 格", level, None

    return _apply(path, change)


@_serialized
def place_entity(
    path: Path, level: str, entity: str, *, x: int, y: int, layer: str | None = None
) -> ActionResult:
    """在 (x, y) 格擺一個 entity。"""

    def change(target: SceneMap) -> tuple[str, str, str | None]:
        placed = edit.place_entity(target, level, entity, x=x, y=y, layer=layer)
        return f"{level}：{entity} 擺在 ({x},{y})，iid {placed['iid']}", level, placed["iid"]

    return _apply(path, change)


@_serialized
def move_entity(path: Path, iid: str, *, x: int, y: int) -> ActionResult:
    """把 entity 移到同一個 level 的 (x, y) 格。"""

    def change(target: SceneMap) -> tuple[str, str, str | None]:
        level, moved = edit.move_entity(target, iid, x=x, y=y)
        name = level["identifier"]
        return f"{name}：{moved['__identifier']} 移到 ({x},{y})", name, None

    return _apply(path, change)


@_serialized
def delete_entity(path: Path, iid: str) -> ActionResult:
    """刪掉一個 entity。"""

    def change(target: SceneMap) -> tuple[str, str, str | None]:
        name = edit.delete_entity(target, iid)["identifier"]
        return f"{name}：刪掉 entity {iid}", name, None

    return _apply(path, change)


@_serialized
def render_level(path: Path, level: str, *, scale: int = 2) -> ActionResult:
    """重新 render 一個 level 的預覽圖，不改動場景地圖。"""
    target = scene.load_scene(path)
    image = rendering.render_level(target, level, scale=scale)
    return ActionResult(f"{level} 的預覽圖", (rendering.write_preview(target, level, image),))
