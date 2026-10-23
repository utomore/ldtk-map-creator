"""CLI 入口：每個操作一個子指令，只轉呼叫 core.actions（P-003）。"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from ..core import actions
from ..core.actions import ActionResult
from ..core.errors import SceneError


def _cell(text: str) -> tuple[int, int]:
    x, _, y = text.partition(",")
    return int(x), int(y)


def _field_value(text: str) -> tuple[str, object]:
    """`名稱=值`；值照 JSON 讀（3、1.5、true、null、"字串"），讀不了就當字串。"""
    name, sep, raw = text.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"欄位要寫成 名稱=值：{text}")
    try:
        return name, json.loads(raw)
    except json.JSONDecodeError:
        return name, raw


def _field_type(text: str) -> tuple[str, str]:
    """`名稱:型別`，例如 target:EntityRef。"""
    name, sep, kind = text.partition(":")
    if not sep:
        raise argparse.ArgumentTypeError(f"欄位要寫成 名稱:型別：{text}")
    return name, kind


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ldtk-map-creator", description="生成並微調 LDtk 場景地圖"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("path", type=Path, help="場景地圖的 .ldtk 主檔")
        return p

    p = command("create_scene", "建一個新的場景地圖")
    p.add_argument("--single", action="store_true", help="存成單檔（預設拆開存）")
    command("describe_scene", "列出 level、圖層、entity")
    p = command("add_level", "新增一個空的 level")
    p.add_argument("identifier")
    p.add_argument("width_cells", type=int)
    p.add_argument("height_cells", type=int)
    p.add_argument("--world-x", type=int, default=0)
    p.add_argument("--world-y", type=int, default=0)
    p = command("paint_rect", "把矩形範圍設成某個值")
    p.add_argument("level")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("value", type=int)
    p.add_argument("--width", type=int, default=1)
    p.add_argument("--height", type=int, default=1)
    p.add_argument("--layer")
    p = command("paint_cells", "把指定的幾格設成某個值")
    p.add_argument("level")
    p.add_argument("value", type=int)
    p.add_argument("cells", type=_cell, nargs="+", help="x,y")
    p.add_argument("--layer")
    p = command("place_entity", "擺一個 entity")
    p.add_argument("level")
    p.add_argument("entity")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--layer")
    p.add_argument("--field", type=_field_value, action="append", default=[], help="名稱=值")
    p = command("move_entity", "移動 entity")
    p.add_argument("iid")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p = command("delete_entity", "刪掉 entity")
    p.add_argument("iid")
    p = command("render_level", "重新 render 預覽圖")
    p.add_argument("level")
    p.add_argument("--scale", type=int, default=2)
    p = command("add_entity_def", "新增一個 entity 定義")
    p.add_argument("identifier")
    p.add_argument("--color", default="#BE4A2F")
    p.add_argument("--width", type=int, default=16)
    p.add_argument("--height", type=int, default=16)
    p.add_argument("--field", type=_field_type, action="append", default=[], help="名稱:型別")
    p = command("set_entity_fields", "設定 entity 的欄位值")
    p.add_argument("iid")
    p.add_argument("fields", type=_field_value, nargs="+", help="名稱=值")
    p = command("generate_scene", "照 <名>.rules.toml 生成場景地圖")
    p.add_argument("--force", action="store_true", help="地圖被直接改過也照規則蓋掉")
    return parser


COMMANDS: dict[str, Callable[[argparse.Namespace], ActionResult]] = {
    "create_scene": lambda a: actions.create_scene(a.path, split=not a.single),
    "describe_scene": lambda a: actions.describe_scene(a.path),
    "add_level": lambda a: actions.add_level(
        a.path,
        a.identifier,
        width_cells=a.width_cells,
        height_cells=a.height_cells,
        world_x=a.world_x,
        world_y=a.world_y,
    ),
    "paint_rect": lambda a: actions.paint_rect(
        a.path, a.level, x=a.x, y=a.y, width=a.width, height=a.height, value=a.value, layer=a.layer
    ),
    "paint_cells": lambda a: actions.paint_cells(
        a.path, a.level, cells=a.cells, value=a.value, layer=a.layer
    ),
    "place_entity": lambda a: actions.place_entity(
        a.path, a.level, a.entity, x=a.x, y=a.y, layer=a.layer, values=dict(a.field) or None
    ),
    "move_entity": lambda a: actions.move_entity(a.path, a.iid, x=a.x, y=a.y),
    "delete_entity": lambda a: actions.delete_entity(a.path, a.iid),
    "render_level": lambda a: actions.render_level(a.path, a.level, scale=a.scale),
    "generate_scene": lambda a: actions.generate_scene(a.path, force=a.force),
    "add_entity_def": lambda a: actions.add_entity_def(
        a.path,
        a.identifier,
        color=a.color,
        width=a.width,
        height=a.height,
        field_types=dict(a.field),
    ),
    "set_entity_fields": lambda a: actions.set_entity_fields(a.path, a.iid, dict(a.fields)),
}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = COMMANDS[args.command](args)
    except SceneError as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1
    print(result.summary)
    for preview in result.previews:
        print(f"預覽圖：{preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
