"""讀規則檔，檢查之後轉成規則資料（D-007、D-009、D-010）。

規則檔寫錯一律丟 SceneError，訊息開頭是出錯的位置，例如 `house_2.size`、`level.House1.near`。
這裡只看規則檔本身；entity 的 type 是不是範本裡的定義，要等生成時對照場景地圖。
"""

import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from . import template
from .errors import SceneError
from .fields import FIELD_TYPES
from .scene import IDENTIFIER, RESERVED_NAMES

Side = Literal["north", "east", "south", "west"]
SIDES: tuple[Side, ...] = ("north", "east", "south", "west")
Cell = tuple[int, int]

_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
EDGE = "edge:"
# 每種元素提供的錨點：`<元素名>.<錨點>`；區域的四邊錨點在那一邊的正中央、區域自己的格裡
AREA_ANCHORS = ("center", *SIDES)
PATH_ANCHORS = ("start", "end")
DEFAULT_GAP = 2
DEFAULT_ENTITY_COLOR = "#BE4A2F"
DEFAULT_ENTITY_SIZE = (16, 16)


@dataclass(frozen=True)
class Kind:
    """圖格種類：元素畫成哪個 IntGrid 值、什麼顏色。"""

    name: str
    value: int
    color: str


@dataclass(frozen=True)
class LevelRule:
    """一個 level：大小，與在世界上的位置（`at` 世界座標，或 `near` 靠近另一個 level）。"""

    name: str
    size: Cell
    at: Cell | None = None
    near: str | None = None
    side: Side | None = None
    gap: int = DEFAULT_GAP


@dataclass(frozen=True)
class AreaRule:
    """區域：一塊矩形；`at` 固定位置，或 `near` 靠近另一個元素。

    `level` 是 None 時在第一個 level（只有一個 level 時可以不寫）。
    """

    name: str
    kind: str
    size: Cell
    at: Literal["center"] | Cell | None = None
    near: str | None = None
    side: Side | None = None
    gap: int = DEFAULT_GAP
    level: str | None = None


@dataclass(frozen=True)
class PathRule:
    """路徑：從一個元素拉到另一個元素，或拉到 level 的一邊（`to = "edge:south"`）。"""

    name: str
    kind: str
    start: str
    to: str
    width: int = 1
    level: str | None = None


@dataclass(frozen=True)
class EntityRule:
    """entity：擺在一個錨點，或固定的格。"""

    name: str
    type: str
    at: str | Cell
    level: str | None = None


@dataclass(frozen=True)
class EntityType:
    """規則檔宣告的 entity 類型（D-010）：顏色、大小（像素）、欄位（名稱 → 型別）。"""

    name: str
    color: str = DEFAULT_ENTITY_COLOR
    size: Cell = DEFAULT_ENTITY_SIZE
    fields: tuple[tuple[str, str], ...] = ()

    def ref_fields(self) -> list[str]:
        return [n for n, t in self.fields if t == "EntityRef"]


@dataclass(frozen=True)
class PortalRule:
    """傳送點（D-010）：兩個錨點各擺一個 `type` 的 entity，用它唯一的 EntityRef 欄位互指。"""

    name: str
    type: str
    start: str
    to: str


@dataclass(frozen=True)
class Rules:
    """一份規則檔的內容；同一種元素照檔案裡的順序。"""

    seed: int
    levels: tuple[LevelRule, ...]
    kinds: tuple[Kind, ...]
    areas: tuple[AreaRule, ...]
    paths: tuple[PathRule, ...]
    entities: tuple[EntityRule, ...]
    entity_types: tuple[EntityType, ...]
    portals: tuple[PortalRule, ...]

    @property
    def level_name(self) -> str:
        """第一個 level 的名稱（元素沒寫 level 時就在這裡）。"""
        return self.levels[0].name

    @property
    def level_size(self) -> Cell:
        """第一個 level 的大小。"""
        return self.levels[0].size

    def level_of(self, element: "AreaRule | PathRule | EntityRule") -> LevelRule:
        """元素在哪個 level；沒寫就是第一個。沒有那個 level 丟 SceneError。"""
        name = element.level or self.levels[0].name
        found = next((lv for lv in self.levels if lv.name == name), None)
        if found is None:
            raise SceneError(f"{element.name}.level：沒有叫 {name} 的 level")
        return found


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
    optional = {"kinds", "area", "path", "entity", "entity_types", "portal"}
    _keys(data, "規則檔", required={"seed", "level"}, optional=optional)
    seed = _int(data["seed"], "seed")
    levels = _levels(data["level"])
    kinds = _kinds(data.get("kinds", {}))
    entity_types = _entity_types(data.get("entity_types", {}))
    areas = tuple(_area(a, f"area[{i}]") for i, a in enumerate(_tables(data, "area")))
    paths = tuple(_path(p, f"path[{i}]") for i, p in enumerate(_tables(data, "path")))
    entities = tuple(_entity(e, f"entity[{i}]") for i, e in enumerate(_tables(data, "entity")))
    portals = tuple(
        _portal(p, f"portal[{i}]", entity_types) for i, p in enumerate(_tables(data, "portal"))
    )
    rules = Rules(seed, levels, kinds, areas, paths, entities, entity_types, portals)
    _check_levels(rules)
    _check_links(rules)
    return rules


# level ---------------------------------------------------------------------


def _levels(raw: object) -> tuple[LevelRule, ...]:
    """`[level]`（M2 的寫法，一個 level）或 `[[level]]`（D-009，多個 level）。"""
    if isinstance(raw, dict):
        # 只有一個 level：可以擺在世界上（at），但沒有別的 level 可以靠近
        table = cast(dict[str, Any], raw)
        _keys(table, "level", required={"name", "size"}, optional={"at"})
        name = _level_name(table["name"], "level.name")
        size = _size(table["size"], "level.size")
        at = _pair(table["at"], "level.at", "[x, y]", minimum=None) if "at" in table else None
        return (LevelRule(name, size, at=at),)
    if not isinstance(raw, list) or not raw:
        raise SceneError(f"level：要寫成 [level] 或至少一個 [[level]]：{raw!r}")
    levels: list[LevelRule] = []
    for i, item in enumerate(cast(list[object], raw)):
        table = _table(item, f"level[{i}]")
        if "name" not in table:
            raise SceneError(f"level[{i}].name：缺少這個欄位")
        name = _level_name(table["name"], f"level[{i}].name")
        where = f"level.{name}"
        _keys(table, where, required={"name", "size"}, optional={"at", "near", "side", "gap"})
        size = _size(table["size"], f"{where}.size")
        _at_or_near(table, where, both_required=False)
        if "at" in table:
            # 世界座標可以是負的（LDtk 的 Free 佈局）
            at = _pair(table["at"], f"{where}.at", "[x, y]", minimum=None)
            levels.append(LevelRule(name, size, at=at))
        elif "near" in table:
            near, side, gap = _near_side_gap(table, where)
            levels.append(LevelRule(name, size, near=near, side=side, gap=gap))
        elif levels:
            raise SceneError(f"{where}.at：第一個以外的 level 要寫 at 或 near")
        else:
            levels.append(LevelRule(name, size))
    return tuple(levels)


def _level_name(value: object, where: str) -> str:
    name = _name(value, where)
    # 拆開存時 level 名稱就是檔名，跟 add_level 擋一樣的名稱
    if name.lower() in RESERVED_NAMES:
        raise SceneError(f"{where}：不能用 Windows 的裝置名稱：{name}")
    return name


def _check_levels(rules: Rules) -> None:
    """level 名稱不重複（不分大小寫）、靠近的 level 存在、不互相靠近成環。"""
    seen: set[str] = set()
    for level in rules.levels:
        if level.name.lower() in seen:
            raise SceneError(f"level.{level.name}.name：level 名稱重複（不分大小寫）")
        seen.add(level.name.lower())
    names = {lv.name for lv in rules.levels}
    edges: dict[str, list[tuple[str, str]]] = {}
    for level in rules.levels:
        edges[level.name] = []
        if level.near is not None:
            if level.near not in names:
                raise SceneError(f"level.{level.name}.near：沒有叫 {level.near} 的 level")
            edges[level.name].append((level.near, "near"))
    _no_cycles(edges, lambda name, field, loop: f"level.{name}.{field}：level 互相靠近成環：{loop}")

    # 都用 at 固定位置的 level，現在就看得出重疊（靠近別人的要排了才知道，交給配置器）
    def world_rect(level: LevelRule) -> tuple[int, int, int, int]:
        x, y = level.at or (0, 0)
        return x, y, level.size[0], level.size[1]

    fixed = [lv for lv in rules.levels if lv.near is None]
    for i, level in enumerate(fixed):
        for other in fixed[:i]:
            if rects_overlap(world_rect(level), world_rect(other)):
                raise SceneError(f"level.{level.name}.at：跟 level {other.name} 重疊")


# 一個元素的欄位 ------------------------------------------------------------


def _area(raw: object, where: str) -> AreaRule:
    table = _table(raw, where)
    name = _element_name(table, where)
    optional = {"at", "near", "side", "gap", "level"}
    _keys(table, name, required={"name", "kind", "size"}, optional=optional)
    kind = _name(table["kind"], f"{name}.kind")
    size = _size(table["size"], f"{name}.size")
    level = _optional_name(table, "level", name)
    _at_or_near(table, name, both_required=True)
    if "at" in table:
        at = table["at"]
        position = "center" if at == "center" else _cell(at, f"{name}.at", '"center" 或 [x, y]')
        return AreaRule(name, kind, size, at=position, level=level)
    near, side, gap = _near_side_gap(table, name)
    return AreaRule(name, kind, size, near=near, side=side, gap=gap, level=level)


def _at_or_near(table: dict[str, Any], where: str, *, both_required: bool) -> None:
    """區域與 level 共用的擺法（D-009）：at 或 near 寫一個；side、gap 只搭配 near。"""
    has_at, has_near = "at" in table, "near" in table
    if has_at and has_near:
        raise SceneError(f"{where}.at：at 與 near 只能寫一個")
    if both_required and not (has_at or has_near):
        raise SceneError(f"{where}.at：at 與 near 要剛好寫一個")
    if not has_near:
        for key in ("side", "gap"):
            if key in table:
                raise SceneError(f"{where}.{key}：只有搭配 near 才能寫")


def _near_side_gap(table: dict[str, Any], where: str) -> tuple[str, Side | None, int]:
    near = _name(table["near"], f"{where}.near")
    side = _side(table.get("side"), f"{where}.side")
    gap = _int(table.get("gap", DEFAULT_GAP), f"{where}.gap", minimum=0)
    return near, side, gap


def _path(raw: object, where: str) -> PathRule:
    table = _table(raw, where)
    name = _element_name(table, where)
    _keys(table, name, required={"name", "kind", "from", "to"}, optional={"width", "level"})
    kind = _name(table["kind"], f"{name}.kind")
    start = _name(table["from"], f"{name}.from")
    to = _str(table["to"], f"{name}.to")
    if to.startswith(EDGE):
        if to.removeprefix(EDGE) not in SIDES:
            raise SceneError(f"{name}.to：邊要是 edge:{'、edge:'.join(SIDES)} 其中一個：{to!r}")
    else:
        _name(to, f"{name}.to")
    width = _int(table.get("width", 1), f"{name}.width", minimum=1)
    return PathRule(name, kind, start, to, width, level=_optional_name(table, "level", name))


def _entity(raw: object, where: str) -> EntityRule:
    table = _table(raw, where)
    name = _element_name(table, where)
    _keys(table, name, required={"name", "type", "at"}, optional={"level"})
    entity_type = _name(table["type"], f"{name}.type")
    level = _optional_name(table, "level", name)
    at = table["at"]
    if isinstance(at, str):
        return EntityRule(name, entity_type, at, level=level)
    cell = _cell(at, f"{name}.at", '錨點（例如 "road.end"）或 [x, y]')
    return EntityRule(name, entity_type, cell, level=level)


def _portal(raw: object, where: str, types: tuple[EntityType, ...]) -> PortalRule:
    table = _table(raw, where)
    name = _element_name(table, where)
    _keys(table, name, required={"name", "from", "to"}, optional={"type"})
    start = _str(table["from"], f"{name}.from")
    to = _str(table["to"], f"{name}.to")
    with_refs = [t for t in types if t.ref_fields()]
    if "type" in table:
        portal_type = _name(table["type"], f"{name}.type")
    elif len(with_refs) == 1:
        portal_type = with_refs[0].name  # 只有一種能互指的類型時可以不寫
    else:
        found = "、".join(t.name for t in with_refs) or "沒有"
        raise SceneError(f"{name}.type：帶 EntityRef 欄位的類型有 {found}，要寫用哪一種")
    portal_ref_field(types, name, portal_type)
    return PortalRule(name, portal_type, start, to)


def portal_ref_field(types: tuple[EntityType, ...], portal: str, portal_type: str) -> str:
    """傳送點用哪個欄位互指：類型要宣告過、剛好一個 EntityRef 欄位（D-010）。

    讀規則檔與生成共用；不合規定丟 SceneError，訊息開頭是 `<傳送點>.type`。
    """
    declared = next((t for t in types if t.name == portal_type), None)
    if declared is None:
        raise SceneError(f"{portal}.type：[entity_types] 裡沒有 {portal_type}")
    refs = declared.ref_fields()
    if len(refs) != 1:
        raise SceneError(
            f"{portal}.type：{portal_type} 要剛好一個 EntityRef 欄位才能互指，現在有 {len(refs)} 個"
        )
    return refs[0]


def _entity_types(raw: object) -> tuple[EntityType, ...]:
    found: list[EntityType] = []
    # 範本已經有的 entity 定義（Spawn）照舊能用，宣告的類型不能跟它們同名
    builtin = {name.lower() for name in template.entity_identifiers()}
    for name, value in _table(raw, "entity_types").items():
        where = f"entity_types.{name}"
        _name(name, where)
        if any(t.name.lower() == name.lower() for t in found):
            raise SceneError(f"{where}：類型名稱重複（不分大小寫）")
        if name.lower() in builtin:
            raise SceneError(f"{where}：範本已經有 {name} 這個 entity 定義，換個名字")
        table = _table(value, where)
        _keys(table, where, required=set(), optional={"color", "size", "fields"})
        color = _str(table.get("color", DEFAULT_ENTITY_COLOR), f"{where}.color")
        if not _COLOR.fullmatch(color):
            raise SceneError(f'{where}.color：要是 "#RRGGBB"：{color!r}')
        size = _size(table.get("size", list(DEFAULT_ENTITY_SIZE)), f"{where}.size")
        fields: list[tuple[str, str]] = []
        for field, kind in _table(table.get("fields", {}), f"{where}.fields").items():
            _name(field, f"{where}.fields.{field}")
            if kind not in FIELD_TYPES:
                types = "、".join(FIELD_TYPES)
                raise SceneError(f"{where}.fields.{field}：型別要是 {types} 其中一個：{kind!r}")
            if any(f.lower() == field.lower() for f, _ in fields):
                raise SceneError(f"{where}.fields.{field}：欄位名稱重複（不分大小寫）")
            fields.append((field, cast(str, kind)))
        found.append(EntityType(name, color, size, tuple(fields)))
    return tuple(found)


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
    """名稱不重複、指向的東西都在而且在同一個 level、不超出 level、互相指向不成環。"""
    elements = (*rules.areas, *rules.paths, *rules.entities, *rules.portals)
    seen: set[str] = set()
    for element in elements:
        if element.name in seen:
            raise SceneError(f"{element.name}.name：元素名稱重複")
        seen.add(element.name)

    level_names = {lv.name for lv in rules.levels}
    for element in (*rules.areas, *rules.paths, *rules.entities):
        if element.level is None and len(rules.levels) > 1:
            raise SceneError(f"{element.name}.level：有好幾個 level 時要寫在哪個 level")
        if element.level is not None and element.level not in level_names:
            raise SceneError(f"{element.name}.level：沒有叫 {element.level} 的 level")

    kinds = {k.name for k in rules.kinds}
    level_for = rules.level_of
    level_of = {e.name: level_for(e).name for e in (*rules.areas, *rules.paths)}
    anchors = {f"{a.name}.{s}": level_of[a.name] for a in rules.areas for s in AREA_ANCHORS}
    anchors |= {f"{p.name}.{s}": level_of[p.name] for p in rules.paths for s in PATH_ANCHORS}

    def target(ref: str, where: str, level: str) -> None:
        if ref not in level_of:
            raise SceneError(f"{where}：沒有叫 {ref} 的區域或路徑")
        if level_of[ref] != level:
            raise SceneError(
                f"{where}：{ref} 在 level {level_of[ref]}，不在 {level}；只有傳送點能跨 level"
            )

    for area in rules.areas:
        level = level_for(area)
        width, height = level.size
        _check_kind(area.kind, kinds, f"{area.name}.kind")
        w, h = area.size
        if w > width or h > height:
            raise SceneError(f"{area.name}.size：{w}x{h} 比 level 的 {width}x{height} 大")
        if isinstance(area.at, tuple) and (area.at[0] + w > width or area.at[1] + h > height):
            raise SceneError(f"{area.name}.at：{area.at} 放 {w}x{h} 會超出 level")
        if area.near is not None:
            target(area.near, f"{area.name}.near", level.name)
    for path in rules.paths:
        level = level_for(path)
        width, height = level.size
        _check_kind(path.kind, kinds, f"{path.name}.kind")
        if path.width > path_width_limit(path, level.size):
            raise SceneError(f"{path.name}.width：{path.width} 比 level 的 {width}x{height} 寬")
        target(path.start, f"{path.name}.from", level.name)
        if not path.to.startswith(EDGE):
            target(path.to, f"{path.name}.to", level.name)
            if path.to == path.start:
                raise SceneError(f"{path.name}.to：起點與終點是同一個元素 {path.to}")
    for entity in rules.entities:
        level = level_for(entity)
        width, height = level.size
        if isinstance(entity.at, tuple):
            x, y = entity.at
            if x >= width or y >= height:
                raise SceneError(f"{entity.name}.at：{entity.at} 超出 level 的 {width}x{height}")
        elif entity.at not in anchors:
            raise SceneError(f"{entity.name}.at：沒有錨點 {entity.at}")
        elif anchors[entity.at] != level.name:
            where = anchors[entity.at]
            raise SceneError(f"{entity.name}.at：{entity.at} 在 level {where}，不在 {level.name}")
    for portal in rules.portals:
        for field, anchor in (("from", portal.start), ("to", portal.to)):
            if anchor not in anchors:
                raise SceneError(f"{portal.name}.{field}：沒有錨點 {anchor}")
        if portal.start == portal.to or _same_cell(rules, portal.start, portal.to):
            raise SceneError(f"{portal.name}.to：兩端是同一格（{portal.start}、{portal.to}）")
    _check_cycles(rules)


def rects_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    """兩個矩形（x, y, 寬, 高）有沒有重疊；只碰到邊不算。"""
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _same_cell(rules: Rules, a: str, b: str) -> bool:
    """同一個區域的兩個錨點會不會落在同一格：區域只有 1 格高時 north 與 south 是同一格。"""
    area_a, _, anchor_a = a.partition(".")
    area_b, _, anchor_b = b.partition(".")
    if area_a != area_b:
        return False
    area = next((x for x in rules.areas if x.name == area_a), None)
    if area is None:
        return False
    return area_anchor_offset(area.size, anchor_a) == area_anchor_offset(area.size, anchor_b)


def area_anchor_offset(size: Cell, anchor: str) -> Cell:
    """區域的錨點在區域裡的哪一格（相對左上角）；配置器照這裡算（D-010）。"""
    w, h = size
    return {
        "center": (w // 2, h // 2),
        "north": (w // 2, 0),
        "south": (w // 2, h - 1),
        "west": (0, h // 2),
        "east": (w - 1, h // 2),
    }[anchor]


def path_width_limit(path: PathRule, level_size: Cell) -> int:
    """路徑最寬能多寬：通到南北邊的是直的，不能比 level 寬；通到東西邊的不能比 level 高。

    兩個元素之間的路徑方向要排了才知道，只擋兩個方向都放不下的。
    """
    width, height = level_size
    side = path.to.removeprefix(EDGE) if path.to.startswith(EDGE) else None
    if side in ("north", "south"):
        return width
    if side in ("east", "west"):
        return height
    return max(width, height)


def _check_kind(kind: str, kinds: set[str], where: str) -> None:
    if kind not in kinds:
        raise SceneError(f"{where}：[kinds] 裡沒有 {kind}")


def references(element: AreaRule | PathRule) -> tuple[tuple[str, str], ...]:
    """元素指向哪些元素：（被指的元素名, 用哪個欄位指）。成環檢查與排列順序共用。"""
    if isinstance(element, AreaRule):
        return ((element.near, "near"),) if element.near else ()
    if element.to.startswith(EDGE):
        return ((element.start, "from"),)
    return ((element.start, "from"), (element.to, "to"))


def _check_cycles(rules: Rules) -> None:
    """區域靠近誰、路徑從哪裡到哪裡，不能繞回自己，不然排不出來。"""
    edges = {e.name: list(references(e)) for e in (*rules.areas, *rules.paths)}
    _no_cycles(edges, lambda name, field, loop: f"{name}.{field}：元素互相指向成環：{loop}")


def _no_cycles(
    edges: dict[str, list[tuple[str, str]]], message: Callable[[str, str, str], str]
) -> None:
    """有向圖不成環；成環時丟 SceneError，訊息由 `message(元素, 欄位, 環)` 決定。

    不用遞迴：參照鏈很長時也不會超過 Python 的遞迴上限。
    """
    done: set[str] = set()
    for root in edges:
        if root in done:
            continue
        stack = [root]
        on_stack = {root}
        todo = [iter(edges[root])]
        while todo:
            step = next(todo[-1], None)
            if step is None:
                name = stack.pop()
                on_stack.discard(name)
                done.add(name)
                todo.pop()
                continue
            ref, field = step
            if ref in on_stack:
                loop = " → ".join([*stack[stack.index(ref) :], ref])
                raise SceneError(message(stack[-1], field, loop))
            if ref not in done:
                stack.append(ref)
                on_stack.add(ref)
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


def _optional_name(table: dict[str, Any], key: str, owner: str) -> str | None:
    return _name(table[key], f"{owner}.{key}") if key in table else None


def _side(value: object, where: str) -> Side | None:
    if value is None:
        return None
    if value not in SIDES:
        raise SceneError(f"{where}：要是 {'、'.join(SIDES)} 其中一個：{value!r}")
    return value


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
    if not IDENTIFIER.fullmatch(text):
        raise SceneError(f"{where}：名稱只能用英數字與底線，且不以數字開頭：{text!r}")
    return text


def _int(value: object, where: str, *, minimum: int | None = None) -> int:
    # bool 是 int 的子類別，要先排除
    if isinstance(value, bool) or not isinstance(value, int):
        raise SceneError(f"{where}：要是整數：{value!r}")
    if minimum is not None and value < minimum:
        raise SceneError(f"{where}：至少是 {minimum}：{value}")
    return value


def _pair(value: object, where: str, expect: str, minimum: int | None) -> Cell:
    if not isinstance(value, list) or len(cast(list[object], value)) != 2:
        raise SceneError(f"{where}：要是 {expect}：{value!r}")
    a, b = cast(list[object], value)
    return _int(a, where, minimum=minimum), _int(b, where, minimum=minimum)


def _size(value: object, where: str) -> Cell:
    return _pair(value, where, "[寬, 高]", minimum=1)


def _cell(value: object, where: str, expect: str) -> Cell:
    return _pair(value, where, expect, minimum=0)
