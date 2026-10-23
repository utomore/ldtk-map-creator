"""讀規則檔，檢查之後轉成規則資料（D-007）。

規則檔寫錯一律丟 SceneError，訊息開頭是出錯的位置，例如 `house_2.size`。
這裡只看規則檔本身；entity 的 type 有沒有定義，要等生成時對照場景地圖。
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from .errors import SceneError

Side = Literal["north", "east", "south", "west"]
SIDES: tuple[Side, ...] = ("north", "east", "south", "west")
Cell = tuple[int, int]

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
_EDGE = "edge:"
# 每種元素提供的錨點：`<元素名>.<錨點>`
AREA_ANCHORS = ("center",)
PATH_ANCHORS = ("start", "end")
DEFAULT_GAP = 2


@dataclass(frozen=True)
class Kind:
    """圖格種類：元素畫成哪個 IntGrid 值、什麼顏色。"""

    name: str
    value: int
    color: str


@dataclass(frozen=True)
class AreaRule:
    """區域：一塊矩形；`at` 固定位置，或 `near` 靠近另一個元素。"""

    name: str
    kind: str
    size: Cell
    at: Literal["center"] | Cell | None = None
    near: str | None = None
    side: Side | None = None
    gap: int = DEFAULT_GAP


@dataclass(frozen=True)
class PathRule:
    """路徑：從一個元素拉到另一個元素，或拉到 level 的一邊（`to = "edge:south"`）。"""

    name: str
    kind: str
    start: str
    to: str
    width: int = 1


@dataclass(frozen=True)
class EntityRule:
    """entity：擺在一個錨點，或固定的格。"""

    name: str
    type: str
    at: str | Cell


@dataclass(frozen=True)
class Rules:
    """一份規則檔的內容；同一種元素照檔案裡的順序。"""

    seed: int
    level_name: str
    level_size: Cell
    kinds: tuple[Kind, ...] = ()
    areas: tuple[AreaRule, ...] = ()
    paths: tuple[PathRule, ...] = ()
    entities: tuple[EntityRule, ...] = ()


def load_rules(path: Path) -> Rules:
    """讀一份規則檔；讀不了或寫錯都丟 SceneError。"""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise SceneError(f"讀不了規則檔 {path}：{error}") from error
    except tomllib.TOMLDecodeError as error:
        raise SceneError(f"規則檔 {path} 不是合法的 TOML：{error}") from error
    return parse_rules(data)


def parse_rules(data: dict[str, Any]) -> Rules:
    """把 TOML 讀出的資料檢查後轉成規則資料。"""
    _keys(data, "規則檔", required={"seed", "level"}, optional={"kinds", "area", "path", "entity"})
    seed = _int(data["seed"], "seed")
    level = _table(data["level"], "level")
    _keys(level, "level", required={"name", "size"})
    level_name = _name(level["name"], "level.name")
    level_size = _size(level["size"], "level.size")

    kinds = _kinds(data.get("kinds", {}))
    areas = tuple(_area(a, f"area[{i}]") for i, a in enumerate(_tables(data, "area")))
    paths = tuple(_path(p, f"path[{i}]") for i, p in enumerate(_tables(data, "path")))
    entities = tuple(_entity(e, f"entity[{i}]") for i, e in enumerate(_tables(data, "entity")))
    rules = Rules(seed, level_name, level_size, kinds, areas, paths, entities)
    _check_links(rules)
    return rules


# 一個元素的欄位 ------------------------------------------------------------


def _area(raw: object, where: str) -> AreaRule:
    table = _table(raw, where)
    name = _element_name(table, where)
    _keys(table, name, required={"name", "kind", "size"}, optional={"at", "near", "side", "gap"})
    kind = _name(table["kind"], f"{name}.kind")
    size = _size(table["size"], f"{name}.size")
    if ("at" in table) == ("near" in table):
        raise SceneError(f"{name}.at：at 與 near 要剛好寫一個")
    if "at" in table:
        for key in ("side", "gap"):
            if key in table:
                raise SceneError(f"{name}.{key}：只有搭配 near 才能寫")
        at = table["at"]
        position = "center" if at == "center" else _cell(at, f"{name}.at", '"center" 或 [x, y]')
        return AreaRule(name, kind, size, at=position)
    near = _name(table["near"], f"{name}.near")
    side = None
    if "side" in table:
        side = table["side"]
        if side not in SIDES:
            raise SceneError(f"{name}.side：要是 {'、'.join(SIDES)} 其中一個：{side!r}")
    gap = _int(table.get("gap", DEFAULT_GAP), f"{name}.gap", minimum=0)
    return AreaRule(name, kind, size, near=near, side=side, gap=gap)


def _path(raw: object, where: str) -> PathRule:
    table = _table(raw, where)
    name = _element_name(table, where)
    _keys(table, name, required={"name", "kind", "from", "to"}, optional={"width"})
    kind = _name(table["kind"], f"{name}.kind")
    start = _name(table["from"], f"{name}.from")
    to = _str(table["to"], f"{name}.to")
    if to.startswith(_EDGE):
        if to.removeprefix(_EDGE) not in SIDES:
            raise SceneError(f"{name}.to：邊要是 edge:{'、edge:'.join(SIDES)} 其中一個：{to!r}")
    else:
        _name(to, f"{name}.to")
    width = _int(table.get("width", 1), f"{name}.width", minimum=1)
    return PathRule(name, kind, start, to, width)


def _entity(raw: object, where: str) -> EntityRule:
    table = _table(raw, where)
    name = _element_name(table, where)
    _keys(table, name, required={"name", "type", "at"})
    entity_type = _name(table["type"], f"{name}.type")
    at = table["at"]
    if isinstance(at, str):
        return EntityRule(name, entity_type, at)
    return EntityRule(
        name, entity_type, _cell(at, f"{name}.at", '錨點（例如 "road.end"）或 [x, y]')
    )


def _kinds(raw: object) -> tuple[Kind, ...]:
    kinds: list[Kind] = []
    for name, value in _table(raw, "kinds").items():
        where = f"kinds.{name}"
        _name(name, where)
        table = _table(value, where)
        _keys(table, where, required={"value", "color"})
        number = _int(table["value"], f"{where}.value", minimum=1)
        if any(k.value == number for k in kinds):
            raise SceneError(f"{where}.value：IntGrid 值 {number} 已經給別的種類用了")
        color = _str(table["color"], f"{where}.color")
        if not _COLOR.fullmatch(color):
            raise SceneError(f'{where}.color：要是 "#RRGGBB"：{color!r}')
        kinds.append(Kind(name, number, color))
    return tuple(kinds)


# 元素之間的關係 ------------------------------------------------------------


def _check_links(rules: Rules) -> None:
    """名稱不重複、指向的東西都在、不超出 level、互相指向不成環。"""
    width, height = rules.level_size
    seen: set[str] = set()
    for element in (*rules.areas, *rules.paths, *rules.entities):
        if element.name in seen:
            raise SceneError(f"{element.name}.name：元素名稱重複")
        seen.add(element.name)

    kinds = {k.name for k in rules.kinds}
    targets = {e.name for e in (*rules.areas, *rules.paths)}
    anchors = {f"{a.name}.{s}" for a in rules.areas for s in AREA_ANCHORS} | {
        f"{p.name}.{s}" for p in rules.paths for s in PATH_ANCHORS
    }

    def target(ref: str, where: str) -> None:
        if ref not in targets:
            raise SceneError(f"{where}：沒有叫 {ref} 的區域或路徑")

    for area in rules.areas:
        _check_kind(area.kind, kinds, f"{area.name}.kind")
        w, h = area.size
        if w > width or h > height:
            raise SceneError(f"{area.name}.size：{w}x{h} 比 level 的 {width}x{height} 大")
        if isinstance(area.at, tuple) and (area.at[0] + w > width or area.at[1] + h > height):
            raise SceneError(f"{area.name}.at：{area.at} 放 {w}x{h} 會超出 level")
        if area.near is not None:
            target(area.near, f"{area.name}.near")
    for path in rules.paths:
        _check_kind(path.kind, kinds, f"{path.name}.kind")
        # 路徑方向要排了才知道，這裡只擋兩個方向都放不下的
        if path.width > max(width, height):
            raise SceneError(f"{path.name}.width：{path.width} 比 level 的 {width}x{height} 寬")
        target(path.start, f"{path.name}.from")
        if not path.to.startswith(_EDGE):
            target(path.to, f"{path.name}.to")
            if path.to == path.start:
                raise SceneError(f"{path.name}.to：起點與終點是同一個元素 {path.to}")
    for entity in rules.entities:
        if isinstance(entity.at, tuple):
            x, y = entity.at
            if x >= width or y >= height:
                raise SceneError(f"{entity.name}.at：{entity.at} 超出 level 的 {width}x{height}")
        elif entity.at not in anchors:
            raise SceneError(f"{entity.name}.at：沒有錨點 {entity.at}")
    _check_cycles(rules)


def _check_kind(kind: str, kinds: set[str], where: str) -> None:
    if kind not in kinds:
        raise SceneError(f"{where}：[kinds] 裡沒有 {kind}")


def _check_cycles(rules: Rules) -> None:
    """區域靠近誰、路徑從哪裡到哪裡，不能繞回自己，不然排不出來。"""
    edges: dict[str, list[tuple[str, str]]] = {}
    for area in rules.areas:
        edges[area.name] = [(area.near, "near")] if area.near else []
    for path in rules.paths:
        edges[path.name] = [(path.start, "from")]
        if not path.to.startswith(_EDGE):
            edges[path.name].append((path.to, "to"))

    # 不用遞迴：參照鏈很長時也不會超過 Python 的遞迴上限
    done: set[str] = set()
    for root in edges:
        if root in done:
            continue
        stack = [root]
        todo = [iter(edges[root])]
        while todo:
            step = next(todo[-1], None)
            if step is None:
                done.add(stack.pop())
                todo.pop()
                continue
            ref, field = step
            if ref in stack:
                loop = " → ".join([*stack[stack.index(ref) :], ref])
                raise SceneError(f"{stack[-1]}.{field}：元素互相指向成環：{loop}")
            if ref not in done:
                stack.append(ref)
                todo.append(iter(edges[ref]))


# 基本型別 ------------------------------------------------------------------


def _keys(
    table: dict[str, Any],
    where: str,
    *,
    required: set[str],
    optional: frozenset[str] | set[str] = frozenset(),
) -> None:
    allowed = required | optional
    for key in table:
        if key not in allowed:
            raise SceneError(f"{where}.{key}：不認得的欄位")
    for key in sorted(required):
        if key not in table:
            raise SceneError(f"{where}.{key}：缺少這個欄位")


def _element_name(table: dict[str, Any], where: str) -> str:
    if "name" not in table:
        raise SceneError(f"{where}.name：缺少這個欄位")
    return _name(table["name"], f"{where}.name")


def _table(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SceneError(f"{where}：要是表格：{value!r}")
    return cast(dict[str, Any], value)


def _tables(data: dict[str, Any], key: str) -> list[object]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise SceneError(f"{key}：要寫成 [[{key}]]：{value!r}")
    return cast(list[object], value)


def _str(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise SceneError(f"{where}：要是字串：{value!r}")
    return value


def _name(value: object, where: str) -> str:
    text = _str(value, where)
    if not _NAME.fullmatch(text):
        raise SceneError(f"{where}：名稱只能用英數字與底線，且不以數字開頭：{text!r}")
    return text


def _int(value: object, where: str, *, minimum: int | None = None) -> int:
    # bool 是 int 的子類別，要先排除
    if isinstance(value, bool) or not isinstance(value, int):
        raise SceneError(f"{where}：要是整數：{value!r}")
    if minimum is not None and value < minimum:
        raise SceneError(f"{where}：至少是 {minimum}：{value}")
    return value


def _pair(value: object, where: str, expect: str, minimum: int) -> Cell:
    if not isinstance(value, list) or len(cast(list[object], value)) != 2:
        raise SceneError(f"{where}：要是 {expect}：{value!r}")
    a, b = cast(list[object], value)
    return _int(a, where, minimum=minimum), _int(b, where, minimum=minimum)


def _size(value: object, where: str) -> Cell:
    return _pair(value, where, "[寬, 高]", minimum=1)


def _cell(value: object, where: str, expect: str) -> Cell:
    return _pair(value, where, expect, minimum=0)
