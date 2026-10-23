"""生成紀錄：記下上次生成的結果，重新生成前拿來比對（D-008）。

存在場景地圖旁的 `<名>.generated.json`（D-002）。只記生成負責寫的內容：
level 的大小、IntGrid 的值定義與每一格、entity 的 iid、定義與位置。
LDtk 存檔時會改的其他欄位不記，在 LDtk 打開再存一次不算改過。
"""

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from .derived import cell_count, refresh_derived
from .errors import SceneError
from .layout import Layout
from .scene import SceneMap

VERSION = 1
_EXAMPLES = 3  # 每一層最多列幾格不同的例子

Content = dict[str, Any]


def rules_path(scene_path: Path) -> Path:
    """場景地圖的規則檔：`<名>.rules.toml`（D-002）。"""
    return scene_path.with_name(f"{scene_path.stem}.rules.toml")


def record_path(scene_path: Path) -> Path:
    """場景地圖的生成紀錄：`<名>.generated.json`（D-002）。"""
    return scene_path.with_name(f"{scene_path.stem}.generated.json")


def content(target: SceneMap) -> Content:
    """場景地圖裡生成負責的內容；兩份相同就表示沒被直接改過。"""
    project = target.project
    refresh_derived(project)  # entity 的格位置從 px 重算，不靠檔案裡可能過期的 __grid
    grid = project["defaultGridSize"]
    layer_defs = {d["uid"]: d for d in project["defs"]["layers"]}
    entity_defs = {d["uid"]: d["identifier"] for d in project["defs"]["entities"]}
    levels: Content = {}
    for level in project["levels"]:
        layers: Content = {}
        entities: list[list[object]] = []
        for layer in level["layerInstances"] or []:
            ldef = layer_defs[layer["layerDefUid"]]
            if ldef["type"] == "IntGrid":
                values = [[v["value"], v["identifier"], v["color"]] for v in ldef["intGridValues"]]
                layers[ldef["identifier"]] = {"values": values, "cells": list(layer["intGridCsv"])}
            for entity in layer["entityInstances"]:
                gx, gy = entity["__grid"]
                entities.append([entity["iid"], entity_defs.get(entity["defUid"]), gx, gy])
        levels[level["identifier"]] = {
            "size": [cell_count(level["pxWid"], grid), cell_count(level["pxHei"], grid)],
            "layers": layers,
            "entities": sorted(entities, key=lambda e: str(e[0])),
        }
    return levels


def make_record(target: SceneMap, placed: Layout) -> dict[str, Any]:
    """生成之後要寫進紀錄的資料：場景地圖的內容，加上每個元素的位置（用來說誰移動了）。"""
    elements: dict[str, str] = {}
    for name, cells in placed.cells.items():
        elements[name] = _digest(sorted(cells))
    for name, cell in placed.entities.items():
        elements[name] = _digest([cell])
    return {"version": VERSION, "content": content(target), "elements": elements}


def dump(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


def read_record(scene_path: Path) -> dict[str, Any] | None:
    """讀生成紀錄；沒有紀錄時回傳 None，讀不了丟 SceneError。"""
    path = record_path(scene_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SceneError(f"讀不了生成紀錄 {path}：{error}") from error
    if not isinstance(data, dict) or cast(dict[str, Any], data).get("version") != VERSION:
        raise SceneError(f"生成紀錄 {path} 的格式不認得")
    return cast(dict[str, Any], data)


def differences(old: Content, new: Content) -> list[str]:
    """兩份內容差在哪裡，給人看的一行一條；相同時是空的。"""
    lines: list[str] = []
    for name in sorted(old.keys() - new.keys()):
        lines.append(f"level {name} 不見了")
    for name in sorted(new.keys() - old.keys()):
        lines.append(f"多了 level {name}")
    for name in sorted(old.keys() & new.keys()):
        lines += [f"{name}：{line}" for line in _level_differences(old[name], new[name])]
    return lines


def _level_differences(old: Content, new: Content) -> list[str]:
    lines: list[str] = []
    if old["size"] != new["size"]:
        lines.append(f"大小 {_size(old['size'])} 變成 {_size(new['size'])}")
    width = new["size"][0]
    for layer in sorted(old["layers"].keys() | new["layers"].keys()):
        before, after = old["layers"].get(layer), new["layers"].get(layer)
        if before is None or after is None:
            lines.append(f"圖層 {layer} {'不見了' if after is None else '是新的'}")
            continue
        if before["values"] != after["values"]:
            lines.append(f"圖層 {layer} 的值定義改了")
        if len(before["cells"]) != len(after["cells"]):
            continue  # 大小變了，上面已經說過
        diff = [
            (i, a, b) for i, (a, b) in enumerate(zip(before["cells"], after["cells"], strict=True))
        ]
        diff = [(i, a, b) for i, a, b in diff if a != b]
        if diff:
            examples = "、".join(
                f"({i % width},{i // width}) {a}→{b}" for i, a, b in diff[:_EXAMPLES]
            )
            more = "…" if len(diff) > _EXAMPLES else ""
            lines.append(f"圖層 {layer} 有 {len(diff)} 格不同：{examples}{more}")
    before_entities = {e[0]: e for e in old["entities"]}
    after_entities = {e[0]: e for e in new["entities"]}
    for iid in sorted(before_entities.keys() - after_entities.keys()):
        _, kind, x, y = before_entities[iid]
        lines.append(f"{kind}（{iid}）在 ({x},{y}) 被刪掉了")
    for iid in sorted(after_entities.keys() - before_entities.keys()):
        _, kind, x, y = after_entities[iid]
        lines.append(f"多了 {kind}（{iid}）在 ({x},{y})")
    for iid in sorted(before_entities.keys() & after_entities.keys()):
        a, b = before_entities[iid], after_entities[iid]
        if a != b:
            lines.append(f"{a[1]}（{iid}）從 ({a[2]},{a[3]}) 變成 {b[1]} 在 ({b[2]},{b[3]})")
    return lines


def moved(
    old: dict[str, Any] | None, new: dict[str, Any]
) -> tuple[list[str], list[str], list[str]]:
    """跟上次生成比：（位置變了的、新的、不見的）元素名稱。"""
    before: dict[str, str] = old["elements"] if old else {}
    after: dict[str, str] = new["elements"]
    changed = sorted(n for n in before.keys() & after.keys() if before[n] != after[n])
    return changed, sorted(after.keys() - before.keys()), sorted(before.keys() - after.keys())


def _digest(cells: list[tuple[int, int]]) -> str:
    return hashlib.sha256(json.dumps(cells).encode()).hexdigest()[:16]


def _size(size: list[int]) -> str:
    return f"{size[0]}x{size[1]}"
