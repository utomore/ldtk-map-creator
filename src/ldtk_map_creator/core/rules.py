"""讀規則檔，檢查之後轉成規則資料（D-007、D-009、D-010、D-011）。

規則檔寫錯一律丟 SceneError，訊息開頭是出錯的位置，例如 `house_2.size`、`level.House1.near`。
除了規則檔本身，也對照範本：`[[entity]]` 的欄位照範本或宣告的定義查，分層慣例的 entity
（Teleport、Part）擺得合不合慣例（D-011）；type 不存在的錯，要等生成時對照場景地圖才報。
"""

import math
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from . import convention, template
from .errors import SceneError
from .fields import FIELD_TYPES, value_fits
from .scene import IDENTIFIER, MAX_GRID, RESERVED_NAMES

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
DEFAULT_GRID = 16


@dataclass(frozen=True)
class LayerValue:
    """地面種類（`[grounds]`）或實心種類（`[collisions]`）：名稱、IntGrid 值、預覽顏色（D-011）。"""

    name: str
    value: int
    color: str


@dataclass(frozen=True)
class Kind:
    """圖格種類（D-011）：元素的格子寫進分層慣例的哪幾層。

    `ground`、`collision` 是 `[grounds]`、`[collisions]` 宣告過的名稱；
    `height` 是 Height 層的階數（0 不畫）；`slope` 為真時 Slope 層畫 1。
    `part` 是零件名稱：區域用了這個種類，生成時在它的中心擺一個 `Part`，方向是 `yaw`（D-013）。
    """

    name: str
    ground: str | None = None
    collision: str | None = None
    height: int = 0
    slope: bool = False
    part: str | None = None
    yaw: float = 0.0


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
    """entity：擺在一個錨點，或固定的格；`fields` 是帶的欄位值（照檔案順序），沒寫的用預設值。

    `size`（格）是這個實例的大小，沒寫就是類型定義的大小；`offset`（像素）是實例在那一格裡
    往右下位移多少，沒寫是 (0, 0)（D-014）。
    """

    name: str
    type: str
    at: str | Cell
    level: str | None = None
    fields: tuple[tuple[str, object], ...] = ()
    size: Cell | None = None
    offset: Cell = (0, 0)


@dataclass(frozen=True)
class EntityType:
    """規則檔宣告的 entity 類型（D-010）：顏色、大小（像素）、欄位（名稱 → 型別）、tags。"""

    name: str
    color: str
    size: Cell  # 像素；規則檔沒寫就是一格大（grid×grid），由讀規則檔的地方填
    fields: tuple[tuple[str, str], ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class FixedEnd:
    """傳送點的固定端點（D-014）：level、左上角的格、大小（格），不靠區域或路徑的錨點。

    `level` 是 None 時在第一個 level（只有一個 level 時可以不寫）。
    """

    level: str | None
    at: Cell
    size: Cell = (1, 1)


@dataclass(frozen=True)
class PortalRule:
    """傳送點（D-010、D-011）：兩端各擺一個 `Teleport`，用 `targetPort` 互指。

    端點是錨點（字串，例如 `house_1.south`），或固定的位置與大小（D-014）。
    """

    name: str
    start: str | FixedEnd
    to: str | FixedEnd


@dataclass(frozen=True)
class ConventionOverride:
    """覆寫分層慣例 entity 的外觀（D-014）：只有顏色與 tags；沒寫的維持範本的值。"""

    name: str
    color: str | None = None
    tags: tuple[str, ...] | None = None


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
    grounds: tuple[LayerValue, ...] = ()
    collisions: tuple[LayerValue, ...] = ()
    grid: int = DEFAULT_GRID  # 格子大小（像素）；生成的專案檔、entity 與 level 的像素都照它
    overrides: tuple[ConventionOverride, ...] = ()  # 分層慣例 entity 的顏色與 tags（D-014）
    split: bool | None = None  # 生成後的存法：True 拆開、False 單檔、None 沿用磁碟上的（D-014）

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

    def end_level(self, end: FixedEnd, where: str) -> LevelRule:
        """固定端點在哪個 level；沒寫就是第一個，有好幾個 level 時一定要寫。`where` 是訊息開頭。"""
        if end.level is None and len(self.levels) != 1:
            raise SceneError(f"{where}.level：有好幾個 level 時要寫在哪個 level")
        name = end.level or self.level_name
        found = next((lv for lv in self.levels if lv.name == name), None)
        if found is None:
            raise SceneError(f"{where}.level：沒有叫 {name} 的 level")
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
    optional = {
        "grid",
        "split",
        "grounds",
        "collisions",
        "kinds",
        "area",
        "path",
        "entity",
        "entity_types",
        "portal",
    }
    _keys(data, "規則檔", required={"seed", "level"}, optional=optional)
    seed = _int(data["seed"], "seed")
    grid = _int(data.get("grid", DEFAULT_GRID), "grid", minimum=1, maximum=MAX_GRID)
    split = _bool(data["split"], "split") if "split" in data else None
    levels = _levels(data["level"])
    grounds = _layer_values(data.get("grounds", {}), "grounds")
    collisions = _layer_values(data.get("collisions", {}), "collisions")
    kinds = _kinds(data.get("kinds", {}), grounds, collisions)
    entity_types, overrides = _entity_types(data.get("entity_types", {}), grid)
    areas = tuple(_area(a, f"area[{i}]") for i, a in enumerate(_tables(data, "area")))
    paths = tuple(_path(p, f"path[{i}]") for i, p in enumerate(_tables(data, "path")))
    entities = tuple(
        _entity(e, f"entity[{i}]", entity_types, grid)
        for i, e in enumerate(_tables(data, "entity"))
    )
    portals = tuple(_portal(p, f"portal[{i}]") for i, p in enumerate(_tables(data, "portal")))
    rules = Rules(
        seed,
        levels,
        kinds,
        areas,
        paths,
        entities,
        entity_types,
        portals,
        grounds,
        collisions,
        grid,
        overrides,
        split,
    )
    _check_levels(rules)
    _check_links(rules)
    _check_part_cells(rules)
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


def _entity(raw: object, where: str, types: tuple[EntityType, ...], grid: int) -> EntityRule:
    table = _table(raw, where)
    name = _element_name(table, where)
    optional = {"level", "fields", "size", "offset"}
    _keys(table, name, required={"name", "type", "at"}, optional=optional)
    entity_type = _name(table["type"], f"{name}.type")
    level = _optional_name(table, "level", name)
    values = _field_values(table.get("fields", {}), name, entity_type, types)
    _check_convention_entity(name, entity_type, dict(values))
    size = _size(table["size"], f"{name}.size") if "size" in table else None
    offset = _offset(table["offset"], f"{name}.offset", grid) if "offset" in table else (0, 0)
    at = table["at"]
    if isinstance(at, str):
        return EntityRule(name, entity_type, at, level, values, size, offset)
    cell = _cell(at, f"{name}.at", '錨點（例如 "road.end"）或 [x, y]')
    return EntityRule(name, entity_type, cell, level, values, size, offset)


def _field_values(
    raw: object, owner: str, entity_type: str, types: tuple[EntityType, ...]
) -> tuple[tuple[str, object], ...]:
    """`[[entity]]` 的 `fields`：欄位名稱要在那個類型的定義裡，值的型別照定義（D-011）。

    類型是範本的三種，或 `[entity_types]` 宣告的；都不是的，類型本身的錯留給生成報，這裡不查欄位。
    EntityRef 欄位不能在這裡寫，傳送點用 `[[portal]]`。
    """
    table = _table(raw, f"{owner}.fields")
    declared = next((t for t in types if t.name == entity_type), None)
    defined = dict(declared.fields) if declared else template.entity_field_types(entity_type)
    if defined is None:
        # 類型不存在的錯留給生成報；值至少要是欄位能存的四種純量，記錄與寫檔才不會壞
        for field, value in table.items():
            if not isinstance(value, bool | int | float | str):
                raise SceneError(f"{owner}.fields.{field}：要是整數、小數、布林或字串：{value!r}")
        return tuple(table.items())
    found: list[tuple[str, object]] = []
    for field, value in table.items():
        where = f"{owner}.fields.{field}"
        kind = defined.get(field)
        if kind is None:
            have = "、".join(defined) or "沒有欄位"
            raise SceneError(f"{where}：{entity_type} 沒有這個欄位（有：{have}）")
        if kind == "EntityRef":
            raise SceneError(f"{where}：EntityRef 欄位不能寫在 fields 裡，傳送點用 [[portal]]")
        if not value_fits(kind, value):
            raise SceneError(f"{where}：要是 {kind}：{value!r}")
        # Float 統一存成小數：90 跟 90.0 是同一個值，寫出的檔與生成紀錄都一樣
        found.append((field, float(cast(float, value)) if kind == "Float" else value))
    return tuple(found)


def _check_convention_entity(name: str, entity_type: str, values: dict[str, object]) -> None:
    """分層慣例的 entity 擺得合不合慣例（D-011）：Teleport 要成對、Part 要寫零件名稱。"""
    if entity_type == convention.TELEPORT:
        raise SceneError(f"{name}.type：{convention.TELEPORT} 要成對互指，用 [[portal]] 擺")
    if entity_type == convention.PART:
        part = values.get(convention.PART_NAME)
        if not isinstance(part, str) or not part.strip():
            raise SceneError(
                f"{name}.fields.{convention.PART_NAME}：{convention.PART} 要寫零件名稱，"
                '例如 fields = { part = "oak" }'
            )


def _portal(raw: object, where: str) -> PortalRule:
    table = _table(raw, where)
    name = _element_name(table, where)
    if "type" in table:
        raise SceneError(f"{name}.type：傳送點一律是 {convention.TELEPORT}（D-011），不用寫 type")
    _keys(table, name, required={"name", "from", "to"})
    start = _end(table["from"], f"{name}.from")
    to = _end(table["to"], f"{name}.to")
    return PortalRule(name, start, to)


def _end(value: object, where: str) -> str | FixedEnd:
    """傳送點的一端：錨點（字串），或固定的位置與大小 `{ level, at, size }`（D-014）。"""
    if not isinstance(value, dict):
        return _str(value, where)
    table = cast(dict[str, Any], value)
    _keys(table, where, required={"at"}, optional={"level", "size"})
    level = _optional_name(table, "level", where)
    at = _cell(table["at"], f"{where}.at", "[x, y]")
    size = _size(table["size"], f"{where}.size") if "size" in table else (1, 1)
    return FixedEnd(level, at, size)


def _entity_types(
    raw: object, grid: int
) -> tuple[tuple[EntityType, ...], tuple[ConventionOverride, ...]]:
    """`[entity_types]`：遊戲自己的類型，加上分層慣例 entity 的覆寫（D-014）。"""
    found: list[EntityType] = []
    overrides: list[ConventionOverride] = []
    # 範本已經有的 entity 定義（分層慣例的三種，D-011）：寫同名的是覆寫它的顏色與 tags
    builtin = {name.lower(): name for name in template.entity_identifiers()}
    for name, value in _table(raw, "entity_types").items():
        where = f"entity_types.{name}"
        _name(name, where)
        if any(t.name.lower() == name.lower() for t in found) or any(
            o.name.lower() == name.lower() for o in overrides
        ):
            raise SceneError(f"{where}：類型名稱重複（不分大小寫）")
        if name.lower() in builtin:
            if name != builtin[name.lower()]:
                raise SceneError(
                    f"{where}：範本已經有 {builtin[name.lower()]} 這個 entity 定義，名稱分大小寫"
                )
            overrides.append(_override(name, _table(value, where), where))
            continue
        table = _table(value, where)
        _keys(table, where, required=set(), optional={"color", "size", "fields", "tags"})
        color = _str(table.get("color", DEFAULT_ENTITY_COLOR), f"{where}.color")
        if not _COLOR.fullmatch(color):
            raise SceneError(f'{where}.color：要是 "#RRGGBB"：{color!r}')
        size = _size(table.get("size", [grid, grid]), f"{where}.size")  # 沒寫就是一格大
        fields: list[tuple[str, str]] = []
        for field, kind in _table(table.get("fields", {}), f"{where}.fields").items():
            _name(field, f"{where}.fields.{field}")
            if kind not in FIELD_TYPES:
                types = "、".join(FIELD_TYPES)
                raise SceneError(f"{where}.fields.{field}：型別要是 {types} 其中一個：{kind!r}")
            if any(f.lower() == field.lower() for f, _ in fields):
                raise SceneError(f"{where}.fields.{field}：欄位名稱重複（不分大小寫）")
            fields.append((field, cast(str, kind)))
        tags = _tags(table.get("tags", []), f"{where}.tags")
        found.append(EntityType(name, color, size, tuple(fields), tags))
    return tuple(found), tuple(overrides)


def _override(name: str, table: dict[str, Any], where: str) -> ConventionOverride:
    """分層慣例 entity 的覆寫：只准 `color`、`tags`；大小與欄位固定（D-011、D-014）。"""
    for key in table:
        if key not in ("color", "tags"):
            raise SceneError(f"{where}.{key}：{name} 是分層慣例的 entity，只能覆寫 color 與 tags")
    if not table:
        raise SceneError(f"{where}：要寫 color 或 tags，{name} 是分層慣例的 entity")
    color = _str(table["color"], f"{where}.color") if "color" in table else None
    tags = _tags(table["tags"], f"{where}.tags") if "tags" in table else None
    override = ConventionOverride(name, color, tags)
    check_override(override, where)
    return override


def check_override(override: ConventionOverride, where: str) -> None:
    """慣例 entity 的覆寫要合法：顏色是 #RRGGBB、tags 不空白不重複、PlayerSpawn 的 tags 有 player。

    讀規則檔時查，生成時也查（直接組出來、沒經過規則檔檢查的規則）。`where` 是訊息開頭。
    """
    if override.color is None and override.tags is None:
        raise SceneError(f"{where}：要寫 color 或 tags，{override.name} 是分層慣例的 entity")
    if override.color is not None and not _COLOR.fullmatch(override.color):
        raise SceneError(f'{where}.color：要是 "#RRGGBB"：{override.color!r}')
    if override.tags is None:
        return
    _tags(list(override.tags), f"{where}.tags")
    if override.name == convention.PLAYER_SPAWN and convention.PLAYER_TAG not in override.tags:
        raise SceneError(
            f"{where}.tags：{override.name} 的 tags 要有 {convention.PLAYER_TAG}（D-011）"
        )


def _layer_values(raw: object, section: str) -> tuple[LayerValue, ...]:
    """`[grounds]`、`[collisions]`：名稱 → 值與預覽顏色；同一節內值與名稱都不重複（D-011）。"""
    found: list[LayerValue] = []
    for name, value in _table(raw, section).items():
        where = f"{section}.{name}"
        _name(name, where)
        if any(v.name.lower() == name.lower() for v in found):
            raise SceneError(f"{where}：名稱重複（不分大小寫）")
        table = _table(value, where)
        _keys(table, where, required={"value", "color"})
        number = _int(table["value"], f"{where}.value", minimum=1)
        taken = next((v for v in found if v.value == number), None)
        if taken is not None:
            raise SceneError(f"{where}.value：值 {number} 已經給 {taken.name} 用了")
        color = _str(table["color"], f"{where}.color")
        if not _COLOR.fullmatch(color):
            raise SceneError(f'{where}.color：要是 "#RRGGBB"：{color!r}')
        found.append(LayerValue(name, number, color))
    return tuple(found)


def _kinds(
    raw: object, grounds: tuple[LayerValue, ...], collisions: tuple[LayerValue, ...]
) -> tuple[Kind, ...]:
    kinds: list[Kind] = []
    for name, value in _table(raw, "kinds").items():
        where = f"kinds.{name}"
        _name(name, where)
        table = _table(value, where)
        for old in ("value", "color"):  # D-011 之前的寫法
            if old in table:
                raise SceneError(
                    f"{where}.{old}：圖格種類改成寫進哪幾層（ground、collision、height、slope）；"
                    "值與顏色改寫在 [grounds]、[collisions]（D-011）"
                )
        _keys(
            table,
            where,
            required=set(),
            optional={"ground", "collision", "height", "slope", "part", "yaw"},
        )
        ground = _declared(table, "ground", where, grounds, "grounds")
        collision = _declared(table, "collision", where, collisions, "collisions")
        height = _int(table.get("height", 0), f"{where}.height", minimum=0)
        if height > convention.MAX_HEIGHT:
            raise SceneError(f"{where}.height：最多 {convention.MAX_HEIGHT} 階：{height}")
        slope = table.get("slope", False)
        if not isinstance(slope, bool):
            raise SceneError(f"{where}.slope：要是 true 或 false：{slope!r}")
        part, yaw = _kind_part(table, where)
        if ground is None and collision is None and height == 0 and not slope and part is None:
            # 只寫 height = 0 或 slope = false 也算：哪一層都沒畫，元素在地圖上看不到
            raise SceneError(
                f"{where}：至少要寫進一層（ground、collision、height 大於 0、slope = true）"
                "或帶零件（part）"
            )
        kinds.append(Kind(name, ground, collision, height, slope, part, yaw))
    return tuple(kinds)


def _kind_part(table: dict[str, Any], where: str) -> tuple[str | None, float]:
    """圖格種類的 `part`（不是空白的字串）與 `yaw`（有限的數字，預設 0；要先有 part）（D-013）。"""
    if "part" not in table:
        if "yaw" in table:
            raise SceneError(f"{where}.yaw：要先寫 part（零件名稱）才能寫方向")
        return None, 0.0
    part = _str(table["part"], f"{where}.part")
    if not part.strip():
        raise SceneError(f"{where}.part：零件名稱不能是空白")
    yaw = table.get("yaw", 0)
    if isinstance(yaw, bool) or not isinstance(yaw, int | float) or not math.isfinite(yaw):
        raise SceneError(f"{where}.yaw：要是數字：{yaw!r}")
    return part, float(yaw)


def _declared(
    table: dict[str, Any], key: str, owner: str, declared: tuple[LayerValue, ...], section: str
) -> str | None:
    """圖格種類的 ground、collision：要是 `[<section>]` 宣告過的名稱。"""
    if key not in table:
        return None
    name = _name(table[key], f"{owner}.{key}")
    if name not in {v.name for v in declared}:
        close = [v.name for v in declared if v.name.lower() == name.lower()]
        hint = f"（名稱分大小寫，是不是 {close[0]}？）" if close else ""
        raise SceneError(f"{owner}.{key}：[{section}] 裡沒有 {name}{hint}")
    return name


# 元素之間的關係 ------------------------------------------------------------


def _check_part_cells(rules: Rules) -> None:
    """entity 或傳送點的錨點指到帶零件區域的 `.center`：那一格是零件的（D-013）。"""
    kinds = {k.name: k for k in rules.kinds}
    parted = {
        f"{a.name}.center": a.name
        for a in rules.areas
        if a.kind in kinds and kinds[a.kind].part is not None
    }
    spots: list[tuple[str, object]] = [(f"{e.name}.at", e.at) for e in rules.entities]
    for p in rules.portals:
        spots += [(f"{p.name}.from", p.start), (f"{p.name}.to", p.to)]
    for where, at in spots:
        if isinstance(at, str) and at in parted:
            raise SceneError(f"{where}：{at} 是 {parted[at]} 的零件擺的格（D-013），換一個錨點")


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
    parted = {k.name for k in rules.kinds if k.part is not None}
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
        if path.kind in parted:
            raise SceneError(f"{path.name}.kind：{path.kind} 帶零件（part），只有區域能用（D-013）")
        if path.width > path_width_limit(path, level.size):
            raise SceneError(f"{path.name}.width：{path.width} 比 level 的 {width}x{height} 寬")
        target(path.start, f"{path.name}.from", level.name)
        if not path.to.startswith(EDGE):
            target(path.to, f"{path.name}.to", level.name)
            if path.to == path.start:
                raise SceneError(f"{path.name}.to：起點與終點是同一個元素 {path.to}")
    for entity in rules.entities:
        level = level_for(entity)
        if isinstance(entity.at, tuple):
            check_entity_rect(rules, entity, entity.at)
        elif entity.at not in anchors:
            raise SceneError(f"{entity.name}.at：沒有錨點 {entity.at}")
        elif anchors[entity.at] != level.name:
            where = anchors[entity.at]
            raise SceneError(f"{entity.name}.at：{entity.at} 在 level {where}，不在 {level.name}")
    for portal in rules.portals:
        for field, end in (("from", portal.start), ("to", portal.to)):
            if isinstance(end, FixedEnd):
                check_fixed_end(rules, f"{portal.name}.{field}", end)
            elif end not in anchors:
                raise SceneError(f"{portal.name}.{field}：沒有錨點 {end}")
        start, to = portal.start, portal.to
        if isinstance(start, FixedEnd) and isinstance(to, FixedEnd):
            first = _fixed_rect(rules, f"{portal.name}.from", start)
            second = _fixed_rect(rules, f"{portal.name}.to", to)
            if first[0] == second[0] and rects_overlap(first[1], second[1]):
                raise SceneError(f"{portal.name}.to：兩端的位置重疊（{start.at}、{to.at}）")
        elif isinstance(start, str) and isinstance(to, str):
            if start == to or _same_cell(rules, start, to):
                raise SceneError(f"{portal.name}.to：兩端是同一格（{start}、{to}）")
    _check_cycles(rules)


def entity_size(rules: Rules, entity: EntityRule) -> Cell | None:
    """entity 實例的大小（像素）：`size`（格）乘以格子大小，沒寫就是類型定義的大小。

    類型不存在（沒宣告、也不是範本的三種）回 None：大小不知道，生成時才會因為類型不存在報錯。
    """
    if entity.size is not None:
        return entity.size[0] * rules.grid, entity.size[1] * rules.grid
    declared = next((t for t in rules.entity_types if t.name == entity.type), None)
    if declared is not None:
        return declared.size
    if entity.type in template.entity_identifiers():
        return rules.grid, rules.grid
    return None


def check_entity_rect(rules: Rules, entity: EntityRule, cell: Cell) -> None:
    """entity 擺在 `cell`（格）時，整個矩形（含 `offset`、`size`）要在它的 level 內（D-014）。

    讀規則檔時查固定的格，配置器再查錨點算出來的格；訊息開頭是 `<元素>.size` 或 `<元素>.at`。
    """
    size = entity_size(rules, entity)
    width, height = rules.level_of(entity).size
    # 讀規則檔時已經擋過；直接組出來的規則也不能悄悄生成出錯的位置
    if any(not 0 <= o < rules.grid for o in entity.offset):
        raise SceneError(
            f"{entity.name}.offset：兩個數字都要在 0 到 {rules.grid - 1}：{list(entity.offset)}"
        )
    if entity.size is not None and min(entity.size) < 1:
        raise SceneError(f"{entity.name}.size：至少是 1：{list(entity.size)}")
    if size is None:  # 類型的大小不知道：至少左上角那一格要在 level 內
        if cell[0] >= width or cell[1] >= height:
            raise SceneError(f"{entity.name}.at：{cell} 超出 level 的 {width}x{height}")
        return
    if entity.size is not None and (entity.size[0] > width or entity.size[1] > height):
        w, h = entity.size
        raise SceneError(f"{entity.name}.size：{w}x{h} 比 level 的 {width}x{height} 大")
    (w, h), grid = size, rules.grid
    x, y = cell[0] * grid + entity.offset[0], cell[1] * grid + entity.offset[1]
    if x + w > width * grid or y + h > height * grid:
        raise SceneError(
            f"{entity.name}.at：{cell} 加上 offset {entity.offset}、大小 {w}x{h} 像素，"
            f"會超出 level 的 {width * grid}x{height * grid} 像素"
        )


def _fixed_rect(rules: Rules, where: str, end: FixedEnd) -> tuple[str, tuple[int, int, int, int]]:
    """固定端點的（level 名稱, 矩形 x、y、寬、高，格）。"""
    level = rules.end_level(end, where)
    return level.name, (*end.at, *end.size)


def check_fixed_end(rules: Rules, where: str, end: FixedEnd) -> None:
    """固定端點要在它的 level 內；讀規則檔時查，配置器也查（D-014）。`where` 是訊息開頭。"""
    level = rules.end_level(end, where)
    width, height = level.size
    (x, y), (w, h) = end.at, end.size
    if min(end.at) < 0:
        raise SceneError(f"{where}.at：不能是負的：{list(end.at)}")
    if min(end.size) < 1:
        raise SceneError(f"{where}.size：至少是 1：{list(end.size)}")
    if w > width or h > height:
        raise SceneError(f"{where}.size：{w}x{h} 比 level 的 {width}x{height} 大")
    if x + w > width or y + h > height:
        raise SceneError(f"{where}.at：{end.at} 放 {w}x{h} 會超出 level 的 {width}x{height}")


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


def _tags(raw: object, where: str) -> tuple[str, ...]:
    """tags：字串陣列；不能有空白的字串，也不能重複。"""
    if not isinstance(raw, list):
        raise SceneError(f"{where}：要是字串陣列：{raw!r}")
    found: list[str] = []
    for tag in cast(list[object], raw):
        text = _str(tag, where)
        if not text.strip():
            raise SceneError(f"{where}：tag 不能是空白：{text!r}")
        if text in found:
            raise SceneError(f"{where}：tag 重複：{text!r}")
        found.append(text)
    return tuple(found)


def _str(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise SceneError(f"{where}：要是字串：{value!r}")
    return value


def _name(value: object, where: str) -> str:
    text = _str(value, where)
    if not IDENTIFIER.fullmatch(text):
        raise SceneError(f"{where}：名稱只能用英數字與底線，且不以數字開頭：{text!r}")
    return text


def _int(
    value: object, where: str, *, minimum: int | None = None, maximum: int | None = None
) -> int:
    # bool 是 int 的子類別，要先排除
    if isinstance(value, bool) or not isinstance(value, int):
        raise SceneError(f"{where}：要是整數：{value!r}")
    if minimum is not None and value < minimum:
        raise SceneError(f"{where}：至少是 {minimum}：{value}")
    if maximum is not None and value > maximum:
        raise SceneError(f"{where}：至多是 {maximum}：{value}")
    return value


def _pair(value: object, where: str, expect: str, minimum: int | None) -> Cell:
    if not isinstance(value, list) or len(cast(list[object], value)) != 2:
        raise SceneError(f"{where}：要是 {expect}：{value!r}")
    a, b = cast(list[object], value)
    return _int(a, where, minimum=minimum), _int(b, where, minimum=minimum)


def _bool(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise SceneError(f"{where}：要是 true 或 false：{value!r}")
    return value


def _offset(value: object, where: str, grid: int) -> Cell:
    """格內的像素位移：兩個整數，各自在 0 到 grid - 1（位移不能把 entity 推出它那一格）。"""
    x, y = _pair(value, where, "[x, y]", minimum=0)
    if x >= grid or y >= grid:
        raise SceneError(f"{where}：兩個數字都要在 0 到 {grid - 1}：{[x, y]}")
    return x, y


def _size(value: object, where: str) -> Cell:
    return _pair(value, where, "[寬, 高]", minimum=1)


def _cell(value: object, where: str, expect: str) -> Cell:
    return _pair(value, where, expect, minimum=0)
