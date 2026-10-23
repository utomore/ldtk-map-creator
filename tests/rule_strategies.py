"""產生合法規則檔的 hypothesis 策略，以及把規則資料轉回 TOML 讀出的樣子。"""

import json
from dataclasses import replace
from typing import Any, cast

from hypothesis import strategies as st

from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.layout import layout
from ldtk_map_creator.core.rules import (
    AREA_ANCHORS,
    DEFAULT_GAP,
    PATH_ANCHORS,
    SIDES,
    AreaRule,
    EntityRule,
    EntityType,
    Kind,
    LevelRule,
    PathRule,
    PortalRule,
    Rules,
)
from ldtk_map_creator.core.scene import RESERVED_NAMES


def width_limit(to: str, level_size: tuple[int, int]) -> int:
    """路徑最寬能多寬，獨立於 core 另寫一次：南北向不能比 level 寬，東西向不能比 level 高。"""
    width, height = level_size
    if to in ("edge:north", "edge:south"):
        return width
    if to in ("edge:east", "edge:west"):
        return height
    return max(width, height)


def one_level_rules(
    seed: int,
    level: str,
    size: tuple[int, int],
    kinds: tuple[Kind, ...],
    areas: tuple[AreaRule, ...],
    paths: tuple[PathRule, ...],
    entities: tuple[EntityRule, ...],
) -> Rules:
    """只有一個 level、沒有 entity 類型與傳送點的規則（M2 的形狀）。"""
    return Rules(seed, (LevelRule(level, size),), kinds, areas, paths, entities, (), ())


names = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,8}", fullmatch=True)


@st.composite
def rules(
    draw: st.DrawFn,
    *,
    entity_types: st.SearchStrategy[str] = names,
    max_side: int = 40,
    min_elements: int = 0,
    max_elements: int = 10,
    max_area: int | None = None,
) -> Rules:
    """合法的規則：每個元素只指向排在它前面的區域或路徑，所以一定不成環。"""
    width, height = draw(st.integers(1, max_side)), draw(st.integers(1, max_side))
    kind_names = draw(st.lists(names, min_size=1, max_size=4, unique=True))
    values = draw(
        st.lists(
            st.integers(1, 99), min_size=len(kind_names), max_size=len(kind_names), unique=True
        )
    )
    colors = st.integers(0, 0xFFFFFF).map(lambda c: f"#{c:06X}")
    kinds = tuple(Kind(n, v, draw(colors)) for n, v in zip(kind_names, values, strict=True))
    element_names = draw(st.lists(names, min_size=min_elements, max_size=max_elements, unique=True))

    areas: list[AreaRule] = []
    paths: list[PathRule] = []
    entities: list[EntityRule] = []
    targets: list[str] = []
    anchors: list[str] = []
    kind = st.sampled_from(kind_names)
    for name in element_names:
        choice = draw(
            st.sampled_from(["area", "path", "entity"] if targets else ["area", "entity"])
        )
        if choice == "area":
            cap = max_area if max_area is not None else max(width, height)
            w, h = draw(st.integers(1, min(width, cap))), draw(st.integers(1, min(height, cap)))
            if targets and draw(st.booleans()):
                area = AreaRule(
                    name,
                    draw(kind),
                    (w, h),
                    near=draw(st.sampled_from(targets)),
                    side=draw(st.none() | st.sampled_from(SIDES)),
                    gap=draw(st.integers(0, 5)),
                )
            elif draw(st.booleans()):
                area = AreaRule(name, draw(kind), (w, h), at="center")
            else:
                at = (draw(st.integers(0, width - w)), draw(st.integers(0, height - h)))
                area = AreaRule(name, draw(kind), (w, h), at=at)
            areas.append(area)
            targets.append(name)
            anchors += [f"{name}.{a}" for a in AREA_ANCHORS]
        elif choice == "path":
            edge = st.sampled_from([f"edge:{s}" for s in SIDES])
            start = draw(st.sampled_from(targets))
            others = [t for t in targets if t != start]
            to = draw(edge | st.sampled_from(others) if others else edge)
            path_width = draw(st.integers(1, width_limit(to, (width, height))))
            paths.append(PathRule(name, draw(kind), start, to, path_width))
            targets.append(name)
            anchors += [f"{name}.{a}" for a in PATH_ANCHORS]
        else:
            cell = st.tuples(st.integers(0, width - 1), st.integers(0, height - 1))
            at = draw(st.sampled_from(anchors) | cell if anchors else cell)
            entities.append(EntityRule(name, draw(entity_types), at))
    seed = draw(st.integers(-(2**63), 2**63 - 1))
    # level 名稱拆開存時就是檔名，Windows 的裝置名稱不合法（RULE-2 會擋）
    level = draw(names.filter(lambda n: n.lower() not in RESERVED_NAMES))
    return one_level_rules(
        seed, level, (width, height), kinds, tuple(areas), tuple(paths), tuple(entities)
    )


def to_toml_data(r: Rules, *, array: bool = False) -> dict[str, Any]:
    """規則資料寫成 TOML 再用 tomllib 讀出來的樣子；選填欄位是預設值時不寫。

    只有一個沒擺位置的 level 時寫成 M2 的 `[level]`；`array=True` 或多個 level 時寫成 `[[level]]`。
    """
    levels: list[dict[str, Any]] = []
    for lv in r.levels:
        level: dict[str, Any] = {"name": lv.name, "size": list(lv.size)}
        if lv.at is not None:
            level["at"] = list(lv.at)
        elif lv.near is not None:
            level["near"] = lv.near
            if lv.side is not None:
                level["side"] = lv.side
            if lv.gap != DEFAULT_GAP:
                level["gap"] = lv.gap
        levels.append(level)
    simple = len(levels) == 1 and set(levels[0]) == {"name", "size"} and not array
    data: dict[str, Any] = {
        "seed": r.seed,
        "level": levels[0] if simple else levels,
        "kinds": {k.name: {"value": k.value, "color": k.color} for k in r.kinds},
    }

    def placed(element: AreaRule | PathRule | EntityRule, item: dict[str, Any]) -> dict[str, Any]:
        if element.level is not None:
            item["level"] = element.level
        return item

    areas: list[dict[str, Any]] = []
    for a in r.areas:
        area: dict[str, Any] = {"name": a.name, "kind": a.kind, "size": list(a.size)}
        if a.at is not None:
            area["at"] = a.at if isinstance(a.at, str) else list(a.at)
        else:
            area["near"] = a.near
            if a.side is not None:
                area["side"] = a.side
            if a.gap != DEFAULT_GAP:
                area["gap"] = a.gap
        areas.append(placed(a, area))
    paths: list[dict[str, Any]] = []
    for p in r.paths:
        path: dict[str, Any] = {"name": p.name, "kind": p.kind, "from": p.start, "to": p.to}
        if p.width != 1:
            path["width"] = p.width
        paths.append(placed(p, path))
    entities = [
        placed(
            e, {"name": e.name, "type": e.type, "at": e.at if isinstance(e.at, str) else list(e.at)}
        )
        for e in r.entities
    ]
    with_refs = [et for et in r.entity_types if et.ref_fields()]
    portals: list[dict[str, Any]] = []
    for portal in r.portals:
        item: dict[str, Any] = {"name": portal.name, "from": portal.start, "to": portal.to}
        # 只有一種能互指的類型時可以不寫 type；這裡用名稱的長度決定寫不寫，兩種寫法都測到
        if not (len(with_refs) == 1 and len(portal.name) % 2 == 0):
            item["type"] = portal.type
        portals.append(item)
    if r.entity_types:
        data["entity_types"] = {
            et.name: {"color": et.color, "size": list(et.size), "fields": dict(et.fields)}
            for et in r.entity_types
        }
    for key, items in (("area", areas), ("path", paths), ("entity", entities), ("portal", portals)):
        if items:
            data[key] = items
    return data


@st.composite
def village_rules(draw: st.DrawFn) -> Rules:
    """像村莊的規則：一兩個固定的區域，其餘大多靠近別人，加上路徑與 entity。

    通用的 `rules()` 多半排不下或很稀疏；配置的 law 要常常碰撞、常常擠開才測得到。
    """
    width, height = draw(st.integers(12, 32)), draw(st.integers(12, 32))
    kinds = (Kind("ground", 1, "#C8B27A"), Kind("house", 2, "#8A4B2F"), Kind("road", 3, "#8E8E8E"))
    size = st.tuples(st.integers(1, 5), st.integers(1, 5))
    count = draw(st.integers(4, 12))
    element_names = [f"e{i}" for i in range(count)]
    areas: list[AreaRule] = [AreaRule("e0", "ground", draw(size), at="center")]
    paths: list[PathRule] = []
    entities: list[EntityRule] = []
    targets = ["e0"]
    anchors = ["e0.center"]
    for name in element_names[1:]:
        choice = draw(st.sampled_from(["near", "near", "near", "fixed", "path", "entity"]))
        if choice == "fixed":
            w, h = draw(size)
            w, h = min(w, width), min(h, height)
            at = (draw(st.integers(0, width - w)), draw(st.integers(0, height - h)))
            areas.append(AreaRule(name, "house", (w, h), at=at))
        elif choice == "near":
            side = draw(st.none() | st.sampled_from(SIDES))
            target = draw(st.sampled_from(targets))
            areas.append(
                AreaRule(
                    name, "house", draw(size), near=target, side=side, gap=draw(st.integers(0, 3))
                )
            )
        elif choice == "path":
            start = draw(st.sampled_from(targets))
            others = [t for t in targets if t != start]
            edges = st.sampled_from([f"edge:{s}" for s in SIDES])
            to = draw(edges | st.sampled_from(others) if others else edges)
            paths.append(PathRule(name, "road", start, to, draw(st.integers(1, 2))))
            anchors += [f"{name}.start", f"{name}.end"]
        else:
            entities.append(EntityRule(name, "Spawn", draw(st.sampled_from(anchors))))
            continue
        if choice != "path":
            anchors.append(f"{name}.center")
        targets.append(name)
    seed = draw(st.integers(0, 2**32))
    return one_level_rules(
        seed, "Village", (width, height), kinds, tuple(areas), tuple(paths), tuple(entities)
    )


def _without(r: Rules, name: str) -> Rules:
    """拿掉一個元素，連同直接或間接指向它的元素與錨點在它上面的 entity。"""
    gone = {name}
    changed = True
    while changed:
        changed = False
        for e in (*r.areas, *r.paths):
            refs = {e.near} if isinstance(e, AreaRule) else {e.start, e.to}
            if e.name not in gone and refs & gone:
                gone.add(e.name)
                changed = True
    keep_entity = [
        e
        for e in r.entities
        if e.name not in gone and not (isinstance(e.at, str) and e.at.split(".")[0] in gone)
    ]
    return replace(
        r,
        areas=tuple(a for a in r.areas if a.name not in gone),
        paths=tuple(p for p in r.paths if p.name not in gone),
        entities=tuple(keep_entity),
    )


@st.composite
def placeable_village_rules(draw: st.DrawFn) -> Rules:
    """排得下的村莊規則：排不下就拿掉出錯的元素再試，不靠 assume 篩掉，免得篩掉太多。"""
    r = draw(village_rules())
    while True:
        try:
            layout(r)
        except SceneError as error:
            name = str(error).split("：", 1)[0].split(".", 1)[0]
            smaller = _without(r, name)
            if smaller == r:  # 訊息沒點名元素：不是排不下，是策略或配置器的錯
                raise
            r = smaller
            continue
        return r


def to_toml_text(r: Rules) -> str:
    """規則資料寫成規則檔的文字；只處理 to_toml_data 會產生的形狀。"""

    def value(v: object) -> str:
        if isinstance(v, str):
            return json.dumps(v, ensure_ascii=False)  # 簡單字串的 JSON 寫法也是合法的 TOML
        if isinstance(v, list):
            return "[" + ", ".join(value(x) for x in cast(list[object], v)) + "]"
        if isinstance(v, dict):
            items = cast(dict[str, object], v).items()
            return "{ " + ", ".join(f"{k} = {value(x)}" for k, x in items) + " }"
        return str(v)

    data = to_toml_data(r)
    lines = [f"seed = {data['seed']}"]
    levels: object = data["level"]
    many = isinstance(levels, list)
    tables = cast(list[dict[str, object]], levels) if many else [cast(dict[str, object], levels)]
    for level in tables:
        lines.append("[[level]]" if many else "[level]")
        lines += [f"{k} = {value(v)}" for k, v in level.items()]
    lines.append("[kinds]")
    lines += [f"{k} = {value(v)}" for k, v in data["kinds"].items()]
    if "entity_types" in data:
        lines.append("[entity_types]")
        lines += [f"{k} = {value(v)}" for k, v in data["entity_types"].items()]
    for key in ("area", "path", "entity", "portal"):
        for item in data.get(key, []):
            lines.append(f"[[{key}]]")
            lines += [f"{k} = {value(v)}" for k, v in item.items()]
    return "\n".join(lines) + "\n"


@st.composite
def multi_rules(draw: st.DrawFn) -> Rules:
    """合法的多 level 規則（D-009、D-010）：1～3 個 level、元素分在各個 level、
    entity 類型與跨 level 的傳送點。元素只指向同一個 level 裡、排在前面的元素，一定不成環。
    """
    level_name = names.filter(lambda n: n.lower() not in RESERVED_NAMES)
    level_names = draw(st.lists(level_name, min_size=1, max_size=3, unique_by=lambda n: n.lower()))
    levels: list[LevelRule] = []
    for i, name in enumerate(level_names):
        size = (draw(st.integers(1, 20)), draw(st.integers(1, 20)))
        how = draw(st.sampled_from(["none", "at"] if i == 0 else ["at", "near"]))
        if how == "none":
            levels.append(LevelRule(name, size))
        elif how == "at":
            levels.append(
                LevelRule(name, size, at=(draw(st.integers(0, 99)), draw(st.integers(0, 99))))
            )
        else:
            near = draw(st.sampled_from(level_names[:i]))
            side = draw(st.none() | st.sampled_from(SIDES))
            levels.append(LevelRule(name, size, near=near, side=side, gap=draw(st.integers(0, 5))))
    kinds = (Kind("ground", 1, "#C8B27A"), Kind("house", 2, "#8A4B2F"))
    kind = st.sampled_from(["ground", "house"])
    element_names = draw(st.lists(names, max_size=12, unique=True))
    areas: list[AreaRule] = []
    paths: list[PathRule] = []
    entities: list[EntityRule] = []
    targets: dict[str, list[str]] = {lv.name: [] for lv in levels}
    anchors: dict[str, list[str]] = {lv.name: [] for lv in levels}
    many = len(levels) > 1
    portal_names: list[str] = []
    for name in element_names:
        lv = draw(st.sampled_from(levels))
        width, height = lv.size
        written = lv.name if many else draw(st.sampled_from([None, lv.name]))
        here = targets[lv.name]
        choice = draw(
            st.sampled_from(
                ["area", "path", "entity", "portal"] if here else ["area", "entity", "portal"]
            )
        )
        if choice == "area":
            w, h = draw(st.integers(1, width)), draw(st.integers(1, height))
            if here and draw(st.booleans()):
                area = AreaRule(
                    name, draw(kind), (w, h), near=draw(st.sampled_from(here)), level=written
                )
            else:
                at = (draw(st.integers(0, width - w)), draw(st.integers(0, height - h)))
                area = AreaRule(name, draw(kind), (w, h), at=at, level=written)
            areas.append(area)
            here.append(name)
            anchors[lv.name] += [f"{name}.{a}" for a in AREA_ANCHORS]
        elif choice == "path":
            start = draw(st.sampled_from(here))
            to = f"edge:{draw(st.sampled_from(SIDES))}"
            paths.append(PathRule(name, draw(kind), start, to, 1, level=written))
            here.append(name)
            anchors[lv.name] += [f"{name}.{a}" for a in PATH_ANCHORS]
        elif choice == "entity":
            cell = st.tuples(st.integers(0, width - 1), st.integers(0, height - 1))
            at = draw(st.sampled_from(anchors[lv.name]) | cell if anchors[lv.name] else cell)
            entities.append(EntityRule(name, "Spawn", at, level=written))
        else:
            portal_names.append(name)
    # entity 類型：一種給傳送點用（剛好一個 EntityRef），另外可能有一種不帶引用的
    field_type = st.sampled_from(["Int", "Float", "Bool", "String"])
    types: list[EntityType] = []
    if draw(st.booleans()) or portal_names:
        extra = tuple((f"f{i}", draw(field_type)) for i in range(draw(st.integers(0, 2))))
        types.append(EntityType("Portal", "#4FC3F7", (16, 16), (("target", "EntityRef"), *extra)))
    if draw(st.booleans()):
        fields = tuple((f"g{i}", draw(field_type)) for i in range(draw(st.integers(0, 3))))
        size = (draw(st.integers(1, 32)), draw(st.integers(1, 32)))
        types.append(EntityType("Sign", "#FFFFFF", size, fields))
    every_anchor = [a for lv in levels for a in anchors[lv.name]]
    portals: list[PortalRule] = []
    if len(every_anchor) >= 2:
        for name in portal_names:
            start, to = draw(
                st.lists(st.sampled_from(every_anchor), min_size=2, max_size=2, unique=True)
            )
            portals.append(PortalRule(name, "Portal", start, to))
    seed = draw(st.integers(-(2**63), 2**63 - 1))
    return Rules(
        seed,
        tuple(levels),
        kinds,
        tuple(areas),
        tuple(paths),
        tuple(entities),
        tuple(types),
        tuple(portals),
    )
