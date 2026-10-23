"""生成紀錄：記下上次生成的結果，重新生成前拿來比對（D-008）。

存在場景地圖旁的 `<名>.generated.json`（D-002）。記的是使用者會改的內容：
圖層、entity、tileset 的定義；每個 level 的大小、世界座標與欄位；
每個圖層的 IntGrid 值定義、每一格與手擺的圖塊；每個 entity 的 iid、定義、位置與欄位。
LDtk 自己會重算或改寫的欄位（`__` 衍生欄位、auto-layer 圖塊、版本號、編輯器設定）不記，
在 LDtk 打開再存一次不算改過。
"""

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, cast

from .derived import cell_count, refresh_derived
from .errors import SceneError
from .layout import Layout
from .scene import SceneMap

VERSION = 3  # 2：加上預覽圖要寫的元素名稱；3：名稱照 level 分開、記下 level 的位置
_DEF_NAMES = {"layers": "圖層", "entities": "entity ", "tilesets": "tileset "}
_EXAMPLES = 3  # 每一層最多列幾格不同的例子

Content = dict[str, Any]


def rules_path(scene_path: Path) -> Path:
    """場景地圖的規則檔：`<名>.rules.toml`（D-002）。"""
    return scene_path.with_name(f"{scene_path.stem}.rules.toml")


def record_path(scene_path: Path) -> Path:
    """場景地圖的生成紀錄：`<名>.generated.json`（D-002）。"""
    return scene_path.with_name(f"{scene_path.stem}.generated.json")


def content(target: SceneMap, *, refreshed: bool = False) -> Content:
    """場景地圖裡使用者會改的內容；兩份相同就表示沒被直接改過。

    在複本上算，不改傳進來的場景地圖；`refreshed=True` 表示呼叫端剛跑過 refresh_derived，
    直接讀、不複製。內容的形狀不對（例如 LDtk 裡刪了還在用的定義）丟 SceneError。
    """
    try:
        return _content(target, refreshed)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise SceneError(f"場景地圖的內容不完整（{error!r}）") from error


def _content(target: SceneMap, refreshed: bool) -> Content:
    project = target.project
    if not refreshed:
        project = copy.deepcopy(project)
        refresh_derived(project)  # entity 的格位置從 px 重算，不靠檔案裡可能過期的 __grid
    grid = project["defaultGridSize"]
    raw = cast(dict[str, Any], project["defs"])
    layer_defs = {d["uid"]: d for d in project["defs"]["layers"]}
    entity_defs = {d["uid"]: d["identifier"] for d in project["defs"]["entities"]}
    defs = {
        "layers": [
            [d["identifier"], d["type"], d["gridSize"], len(d["autoRuleGroups"])]
            for d in project["defs"]["layers"]
        ],
        "entities": [
            [d["identifier"], d["width"], d["height"], len(d["fieldDefs"])]
            for d in project["defs"]["entities"]
        ],
        "tilesets": [[d.get("identifier"), d.get("relPath")] for d in raw.get("tilesets", [])],
    }
    levels: Content = {}
    for level in project["levels"]:
        layers: Content = {}
        entities: list[list[object]] = []
        for layer in level["layerInstances"] or []:
            ldef = layer_defs[layer["layerDefUid"]]
            raw_layer = cast(dict[str, Any], layer)
            entry: Content = {
                "type": ldef["type"],
                "width": cell_count(level["pxWid"], ldef["gridSize"]),
                "settings": [
                    raw_layer.get("pxOffsetX"),
                    raw_layer.get("pxOffsetY"),
                    raw_layer.get("visible"),
                    raw_layer.get("opacity"),
                ],
            }
            if ldef["type"] == "IntGrid":
                # LDtk 存檔可能改顏色的大小寫，統一成大寫再比
                entry["values"] = [
                    [v["value"], v["identifier"], str(v["color"]).upper()]
                    for v in ldef["intGridValues"]
                ]
                entry["cells"] = list(layer["intGridCsv"])
            tiles = raw_layer.get("gridTiles", [])
            entry["tiles"] = sorted(
                [t.get("px"), t.get("src"), t.get("f"), t.get("t")] for t in tiles
            )
            layers[ldef["identifier"]] = entry
            for entity in layer["entityInstances"]:
                gx, gy = entity["__grid"]
                kind = entity_defs.get(entity["defUid"])
                # 格內的像素位置與大小也記，在 LDtk 裡挪幾個像素或改大小也算改過
                shape = [*entity["px"], entity["width"], entity["height"]]
                entities.append([entity["iid"], kind, gx, gy, [_fields(entity), shape]])
        levels[level["identifier"]] = {
            "size": [cell_count(level["pxWid"], grid), cell_count(level["pxHei"], grid)],
            "world": [level["worldX"], level["worldY"]],
            "fields": _fields(level),
            "layers": layers,
            "entities": sorted(entities, key=lambda e: str(e[0])),
        }
    return {"defs": defs, "levels": levels}


def _fields(owner: object) -> list[list[object]]:
    found = cast(dict[str, Any], owner).get("fieldInstances", [])
    return [[f.get("defUid"), f.get("__value")] for f in found]


def make_record(target: SceneMap, placed: Layout, areas: set[str]) -> dict[str, Any]:
    """生成之後要寫進紀錄的資料：場景地圖的內容、每個元素與 level 的位置（用來說誰移動了），
    以及每個 level 的預覽圖要寫的元素名稱。
    """
    elements: dict[str, str] = {}
    for name, cells in placed.cells.items():
        elements[name] = _digest([placed.level_of[name], sorted(cells)])
    for name, cell in placed.entities.items():
        elements[name] = _digest([placed.level_of[name], [cell]])
    # level 的名稱是識別字，不會有空白；加上前綴跟元素分開
    for name, rect in placed.levels.items():
        elements[f"level {name}"] = _digest(list(rect))
    by_level: dict[str, list[list[object]]] = {name: [] for name in placed.levels}
    for name, x, y in name_positions(placed, areas):
        by_level[placed.level_of[name]].append([name, x, y])
    return {
        "version": VERSION,
        "content": content(target),
        "elements": elements,
        "labels": by_level,
    }


def name_positions(placed: Layout, areas: set[str]) -> list[tuple[str, float, float]]:
    """預覽圖上每個區域、路徑的名稱寫在哪裡（格座標）。

    `areas` 是區域的名稱，其餘是路徑。區域寫在正中央；
    路徑寫在自己的格裡、離外框中心最近的一格，L 形路才不會寫到空地上。
    """
    found: list[tuple[str, float, float]] = []
    for name, cells in placed.cells.items():
        xs = [x for x, _ in cells]
        ys = [y for _, y in cells]
        cx, cy = (min(xs) + max(xs) + 1) / 2, (min(ys) + max(ys) + 1) / 2
        if name not in areas:
            x, y = min(
                cells, key=lambda c: (abs(c[0] + 0.5 - cx) + abs(c[1] + 0.5 - cy), c[1], c[0])
            )
            cx, cy = x + 0.5, y + 0.5
        found.append((name, cx, cy))
    return found


def labels(saved: dict[str, Any] | None, level: str) -> list[tuple[str, float, float]]:
    """從生成紀錄取出某個 level 要寫的元素名稱；沒有紀錄或不是生成的 level 就是空的。"""
    found = saved.get("labels") if saved else None
    if not isinstance(found, dict):
        return []
    try:
        listed = cast(dict[str, Any], found).get(level, [])
        names = [(str(n), float(x), float(y)) for n, x, y in listed]
    except (KeyError, TypeError, ValueError):
        return []
    # JSON 讀得進 NaN、Infinity，這種位置畫不出來，略過
    return [(n, x, y) for n, x, y in names if math.isfinite(x) and math.isfinite(y)]


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
    record = cast(dict[str, Any], data) if isinstance(data, dict) else {}
    body = record.get("content")
    if (
        record.get("version") != VERSION
        or not isinstance(record.get("elements"), dict)
        or not isinstance(body, dict)
        or set(cast(dict[str, Any], body)) != {"defs", "levels"}
    ):
        raise SceneError(f"生成紀錄 {path} 的格式不認得；帶 force 照規則重新生成就會重寫")
    return record


def differences(old: Content, new: Content) -> list[str]:
    """兩份內容差在哪裡，給人看的一行一條；相同時是空的。紀錄壞掉時丟 SceneError。"""
    try:
        return _differences(old, new)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        raise SceneError(
            f"生成紀錄的內容不認得（{error!r}）；帶 force 照規則重新生成就會重寫"
        ) from error


def _differences(old_content: Content, new_content: Content) -> list[str]:
    lines: list[str] = []
    for part in ("layers", "entities", "tilesets"):
        if old_content["defs"][part] != new_content["defs"][part]:
            lines.append(f"{_DEF_NAMES[part]}定義改了")
    old, new = old_content["levels"], new_content["levels"]
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
    if old["world"] != new["world"]:
        lines.append(f"世界座標 {tuple(old['world'])} 變成 {tuple(new['world'])}")
    if old["fields"] != new["fields"]:
        lines.append("level 的欄位改了")
    same_size = old["size"] == new["size"]
    for layer in sorted(old["layers"].keys() | new["layers"].keys()):
        before, after = old["layers"].get(layer), new["layers"].get(layer)
        if before is None or after is None:
            lines.append(f"圖層 {layer} {'不見了' if after is None else '是新的'}")
            continue
        if before.get("values") != after.get("values"):
            lines.append(f"圖層 {layer} 的值定義改了")
        if before["tiles"] != after["tiles"]:
            lines.append(f"圖層 {layer} 的圖塊改了")
        if before["settings"] != after["settings"]:
            lines.append(f"圖層 {layer} 的偏移或顯示設定改了")
        if not same_size or "cells" not in before or "cells" not in after:
            continue  # 大小變了，上面已經說過；格子的座標對不上，不逐格比
        if before["width"] != after["width"] or len(before["cells"]) != len(after["cells"]):
            lines.append(f"圖層 {layer} 的格子大小改了")
            continue
        width = after["width"]
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
        _, kind, x, y, _ = before_entities[iid]
        lines.append(f"{kind}（{iid}）在 ({x},{y}) 被刪掉了")
    for iid in sorted(after_entities.keys() - before_entities.keys()):
        _, kind, x, y, _ = after_entities[iid]
        lines.append(f"多了 {kind}（{iid}）在 ({x},{y})")
    for iid in sorted(before_entities.keys() & after_entities.keys()):
        a, b = before_entities[iid], after_entities[iid]
        if a[:4] != b[:4]:
            lines.append(f"{a[1]}（{iid}）從 ({a[2]},{a[3]}) 變成 {b[1]} 在 ({b[2]},{b[3]})")
        elif a != b:
            lines.append(f"{a[1]}（{iid}）的像素位置、大小或欄位改了")
    return lines


def moved(
    old: dict[str, Any] | None, new: dict[str, Any]
) -> tuple[list[str], list[str], list[str]]:
    """跟上次生成比：（位置變了的、新的、不見的）元素名稱。"""
    before: dict[str, str] = old["elements"] if old else {}
    after: dict[str, str] = new["elements"]
    changed = sorted(n for n in before.keys() & after.keys() if before[n] != after[n])
    return changed, sorted(after.keys() - before.keys()), sorted(before.keys() - after.keys())


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()[:16]


def _size(size: list[int]) -> str:
    return f"{size[0]}x{size[1]}"
