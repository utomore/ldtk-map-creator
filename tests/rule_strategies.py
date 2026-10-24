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
    LayerValue,
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
    grounds: tuple[LayerValue, ...] = (),
    collisions: tuple[LayerValue, ...] = (),
) -> Rules:
    """只有一個 level、沒有 entity 類型與傳送點的規則（M2 的形狀）。"""
    return Rules(
        seed, (LevelRule(level, size),), kinds, areas, paths, entities, (), (), grounds, collisions
    )


# 固定的值定義與圖格種類（村莊形狀的規則用，D-011）：有地面、實心、高度、斜坡的各種組合
VILLAGE_GROUNDS = (LayerValue("grass", 1, "#7CB342"), LayerValue("dirt", 2, "#C8B27A"))
VILLAGE_COLLISIONS = (LayerValue("wall", 1, "#000000"), LayerValue("water", 2, "#2F4CBE"))
VILLAGE_KINDS = (
    Kind("ground", ground="dirt"),
    Kind("house", ground="dirt", collision="wall"),
    Kind("road", ground="grass"),
    Kind("hill", ground="grass", height=2),
    Kind("ramp", ground="grass", height=1, slope=True),
    Kind("pond", collision="water"),
)
# 只有一種圖格種類 k、一種地面 g 的規則（單元測試用）
SIMPLE_GROUNDS = (LayerValue("g", 1, "#000000"),)
SIMPLE_KINDS = (Kind("k", ground="g"),)
# 區域用的種類（路徑一律是 road）
AREA_KINDS = ["ground", "house", "hill", "ramp", "pond"]


# 固定位置 level 的格位：彼此隔 25 格，level 最大 20 格，避開第一個 level 預設的 (0, 0)
SLOTS = [(x * 25, y * 25) for x in range(-3, 4) for y in range(-3, 4) if (x, y) != (0, 0)]

names = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,8}", fullmatch=True)
PART_OR_NAME = names | st.just("Part")

# 範本 Part 的欄位（D-011），獨立於 core 另寫一次
PART_FIELDS = (("part", "String"), ("yaw", "Float"))


def scalar_value(kind: str) -> st.SearchStrategy[object]:
    """某個型別的欄位值（Int、Float、Bool、String）；Float 也收整數。"""
    return {
        "Int": st.integers(-(10**6), 10**6),
        "Float": st.integers(-(10**6), 10**6) | st.floats(-1e6, 1e6, allow_nan=False),
        "Bool": st.booleans(),
        "String": st.text("abcXYZ019 _", max_size=8),
    }[kind]


@st.composite
def field_values(
    draw: st.DrawFn, defined: tuple[tuple[str, str], ...]
) -> tuple[tuple[str, object], ...]:
    """`[[entity]]` 的 fields：從欄位定義挑一部分（EntityRef 除外），順序任意，值的型別對。"""
    usable = [f for f in defined if f[1] != "EntityRef"]
    if not usable:
        return ()
    picked = draw(st.lists(st.sampled_from(usable), unique_by=lambda f: f[0], max_size=len(usable)))
    return tuple((name, draw(scalar_value(kind))) for name, kind in picked)


@st.composite
def part_fields(draw: st.DrawFn) -> tuple[tuple[str, object], ...]:
    """Part 的 fields：一定有不是空白的 part（D-011），有時加 yaw，順序任意。"""
    items: list[tuple[str, object]] = [
        ("part", draw(st.text("abcXYZ019_", min_size=1, max_size=8)))
    ]
    if draw(st.booleans()):
        items.append(("yaw", draw(scalar_value("Float"))))
    return tuple(draw(st.permutations(items)))


@st.composite
def rules(
    draw: st.DrawFn,
    *,
    entity_types: st.SearchStrategy[str] = PART_OR_NAME,
    max_side: int = 40,
    min_elements: int = 0,
    max_elements: int = 10,
    max_area: int | None = None,
) -> Rules:
    """合法的規則：每個元素只指向排在它前面的區域或路徑，所以一定不成環。"""
    width, height = draw(st.integers(1, max_side)), draw(st.integers(1, max_side))
    colors = st.integers(0, 0xFFFFFF).map(lambda c: f"#{c:06X}")
    grounds = draw(layer_values(colors))
    collisions = draw(layer_values(colors))
    kind_names = draw(st.lists(names, min_size=1, max_size=4, unique=True))
    kinds = tuple(
        Kind(
            n,
            draw(st.none() | st.sampled_from([v.name for v in grounds])) if grounds else None,
            draw(st.none() | st.sampled_from([v.name for v in collisions])) if collisions else None,
            draw(st.integers(0, 4)),
            draw(st.booleans()),
        )
        for n in kind_names
    )
    # 每個種類至少寫一層（RULE-2）：什麼都沒寫的補上高度 1
    kinds = tuple(k if _writes(k) else replace(k, height=1) for k in kinds)
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
            entity_type = draw(entity_types)
            fields = draw(part_fields()) if entity_type == "Part" else ()
            entities.append(EntityRule(name, entity_type, at, fields=fields))
    seed = draw(st.integers(-(2**63), 2**63 - 1))
    # level 名稱拆開存時就是檔名，Windows 的裝置名稱不合法（RULE-2 會擋）
    level = draw(names.filter(lambda n: n.lower() not in RESERVED_NAMES))
    return qualified_slopes(
        one_level_rules(
            seed,
            level,
            (width, height),
            kinds,
            tuple(areas),
            tuple(paths),
            tuple(entities),
            grounds,
            collisions,
        )
    )


def stranded_elements(r: Rules) -> set[str] | None:
    """有斜坡格四周沒有高一階的格的元素（D-012）；排不下回傳 None。

    獨立於 core 另寫一次：先用配置器算格，再照圖格種類的 height 算每一格的高度。
    """
    try:
        placed = layout(r)
    except SceneError:
        return None
    kinds = {k.name: k for k in r.kinds}
    kind_of = {e.name: kinds[e.kind] for e in (*r.areas, *r.paths)}
    heights: dict[tuple[str, int, int], int] = {}
    for name, cells in placed.cells.items():
        for x, y in cells:
            heights[(placed.level_of[name], x, y)] = kind_of[name].height
    bad: set[str] = set()
    for name, cells in placed.cells.items():
        if not kind_of[name].slope:
            continue
        level = placed.level_of[name]
        for x, y in cells:
            here = heights[(level, x, y)]
            around = ((x, y - 1), (x + 1, y), (x, y + 1), (x - 1, y))
            if not any(heights.get((level, nx, ny), 0) == here + 1 for nx, ny in around):
                bad.add(name)
                break
    return bad


def qualified_slopes(r: Rules) -> Rules:
    """把不合格的斜坡元素改用不是斜坡的分身種類（D-012），好讓生成一定成功。

    分身的高度至少 1（高度 0 的斜坡變成一階的平台），所以這些格的 Height 會跟原本不同。

    其他元素的種類不動，所以合格的斜坡還在；排不下的規則原樣回傳。
    """
    while True:
        bad = stranded_elements(r)
        if not bad:
            return r
        taken = {k.name for k in r.kinds}
        flat: dict[str, str] = {}
        extra: list[Kind] = []
        needed = {e.kind for e in (*r.areas, *r.paths) if e.name in bad}
        for k in r.kinds:
            if k.name in needed and k.name not in flat:
                twin = f"{k.name}_flat"
                while twin in taken:
                    twin += "_"
                taken.add(twin)
                flat[k.name] = twin
                extra.append(replace(k, name=twin, slope=False, height=max(k.height, 1)))
        areas = tuple(replace(a, kind=flat[a.kind]) if a.name in bad else a for a in r.areas)
        paths = tuple(replace(p, kind=flat[p.kind]) if p.name in bad else p for p in r.paths)
        r = replace(r, kinds=(*r.kinds, *extra), areas=areas, paths=paths)


def _writes(kind: Kind) -> bool:
    """圖格種類有沒有寫進任何一層（寫成規則檔時至少要有一個欄位）。"""
    return kind.ground is not None or kind.collision is not None or kind.height > 0 or kind.slope


@st.composite
def layer_values(draw: st.DrawFn, colors: st.SearchStrategy[str]) -> tuple[LayerValue, ...]:
    """`[grounds]` 或 `[collisions]`：0 到 3 個，名稱（不分大小寫）與值都不重複。"""
    found = draw(st.lists(names, max_size=3, unique_by=lambda n: n.lower()))
    values = draw(
        st.lists(st.integers(1, 99), min_size=len(found), max_size=len(found), unique=True)
    )
    return tuple(LayerValue(n, v, draw(colors)) for n, v in zip(found, values, strict=True))


def kind_data(k: Kind) -> dict[str, Any]:
    """圖格種類寫成 TOML 讀出的樣子；沒寫的（預設值）不出現。"""
    item: dict[str, Any] = {}
    if k.ground is not None:
        item["ground"] = k.ground
    if k.collision is not None:
        item["collision"] = k.collision
    if k.height != 0:
        item["height"] = k.height
    if k.slope:
        item["slope"] = True
    return item


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
        "kinds": {k.name: kind_data(k) for k in r.kinds},
    }
    for key, values in (("grounds", r.grounds), ("collisions", r.collisions)):
        if values:
            data[key] = {v.name: {"value": v.value, "color": v.color} for v in values}

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
    entities: list[dict[str, Any]] = []
    for e in r.entities:
        entity: dict[str, Any] = {
            "name": e.name,
            "type": e.type,
            "at": e.at if isinstance(e.at, str) else list(e.at),
        }
        if e.fields:
            entity["fields"] = dict(e.fields)
        entities.append(placed(e, entity))
    portals = [{"name": p.name, "from": p.start, "to": p.to} for p in r.portals]
    if r.entity_types:
        data["entity_types"] = {
            et.name: {"color": et.color, "size": list(et.size), "fields": dict(et.fields)}
            for et in r.entity_types
        }
    for key, items in (("area", areas), ("path", paths), ("entity", entities), ("portal", portals)):
        if items:
            data[key] = items
    return data


def village_rules() -> st.SearchStrategy[Rules]:
    """像村莊的規則，斜坡都合格（D-012）。"""
    return _raw_village_rules().map(qualified_slopes)


@st.composite
def _raw_village_rules(draw: st.DrawFn) -> Rules:
    """像村莊的規則（斜坡還沒保證合格）：一兩個固定的區域，其餘大多靠近別人，加上路徑與 entity。

    通用的 `rules()` 多半排不下或很稀疏；配置的 law 要常常碰撞、常常擠開才測得到。
    """
    width, height = draw(st.integers(12, 32)), draw(st.integers(12, 32))
    kinds = VILLAGE_KINDS
    size = st.tuples(st.integers(1, 5), st.integers(1, 5))
    count = draw(st.integers(4, 12))
    element_names = [f"e{i}" for i in range(count)]
    area_kind = st.sampled_from(AREA_KINDS)
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
            areas.append(AreaRule(name, draw(area_kind), (w, h), at=at))
        elif choice == "near":
            side = draw(st.none() | st.sampled_from(SIDES))
            target = draw(st.sampled_from(targets))
            areas.append(
                AreaRule(
                    name,
                    draw(area_kind),
                    draw(size),
                    near=target,
                    side=side,
                    gap=draw(st.integers(0, 3)),
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
            at = draw(st.sampled_from(anchors))
            if draw(st.booleans()):
                entities.append(EntityRule(name, "Part", at, fields=draw(part_fields())))
            else:
                entities.append(EntityRule(name, "PlayerSpawn", at))
            continue
        if choice != "path":
            anchors.append(f"{name}.center")
        targets.append(name)
    seed = draw(st.integers(0, 2**32))
    return one_level_rules(
        seed,
        "Village",
        (width, height),
        kinds,
        tuple(areas),
        tuple(paths),
        tuple(entities),
        VILLAGE_GROUNDS,
        VILLAGE_COLLISIONS,
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
    # 傳送點：點名的就是它，或兩端有一端的錨點在拿掉的元素上
    keep_portal = [
        p
        for p in r.portals
        if p.name not in gone and not {p.start.split(".")[0], p.to.split(".")[0]} & gone
    ]
    return replace(
        r,
        areas=tuple(a for a in r.areas if a.name not in gone),
        paths=tuple(p for p in r.paths if p.name not in gone),
        entities=tuple(keep_entity),
        portals=tuple(keep_portal),
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
        return qualified_slopes(r)


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
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    data = to_toml_data(r)
    lines = [f"seed = {data['seed']}"]
    levels: object = data["level"]
    many = isinstance(levels, list)
    tables = cast(list[dict[str, object]], levels) if many else [cast(dict[str, object], levels)]
    for level in tables:
        lines.append("[[level]]" if many else "[level]")
        lines += [f"{k} = {value(v)}" for k, v in level.items()]
    for key in ("grounds", "collisions"):
        if key in data:
            lines.append(f"[{key}]")
            lines += [f"{k} = {value(v)}" for k, v in data[key].items()]
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
    used_slots: list[tuple[int, int]] = []
    for i, name in enumerate(level_names):
        size = (draw(st.integers(1, 20)), draw(st.integers(1, 20)))
        how = draw(st.sampled_from(["none", "at"] if i == 0 else ["at", "near"]))
        if how == "none":
            levels.append(LevelRule(name, size))
        elif how == "at":
            # 固定位置的 level 不能重疊：每個 level 佔一個 25 格見方的格位，包含負的座標
            slot = draw(st.sampled_from([s for s in SLOTS if s not in used_slots]))
            used_slots.append(slot)
            levels.append(LevelRule(name, size, at=slot))
        else:
            near = draw(st.sampled_from(level_names[:i]))
            side = draw(st.none() | st.sampled_from(SIDES))
            levels.append(LevelRule(name, size, near=near, side=side, gap=draw(st.integers(0, 5))))
    kinds = VILLAGE_KINDS
    kind = st.sampled_from(AREA_KINDS)
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
            entities.append(EntityRule(name, "PlayerSpawn", at, level=written))
        else:
            portal_names.append(name)
    # 宣告的 entity 類型（遊戲自己的）：一種帶 EntityRef 欄位，一種只有一般欄位
    field_type = st.sampled_from(["Int", "Float", "Bool", "String"])
    types: list[EntityType] = []
    if draw(st.booleans()):
        extra = tuple((f"f{i}", draw(field_type)) for i in range(draw(st.integers(0, 2))))
        types.append(EntityType("Marker", "#4FC3F7", (16, 16), (("link", "EntityRef"), *extra)))
    if draw(st.booleans()):
        fields = tuple((f"g{i}", draw(field_type)) for i in range(draw(st.integers(0, 3))))
        size = (draw(st.integers(1, 32)), draw(st.integers(1, 32)))
        types.append(EntityType("Sign", "#FFFFFF", size, fields))
    every_anchor = [a for lv in levels for a in anchors[lv.name]]
    sizes = {area.name: area.size for area in areas}

    def anchor_cell(anchor: str) -> tuple[str, int, int] | None:
        """區域錨點在區域裡的哪一格，獨立於 core 另算；路徑的錨點回傳 None（排了才知道）。"""
        owner, _, which = anchor.partition(".")
        if owner not in sizes:
            return None
        w, h = sizes[owner]
        x = {"west": 0, "east": w - 1}.get(which, w // 2)
        y = {"north": 0, "south": h - 1}.get(which, h // 2)
        return owner, x, y

    # 傳送點兩端不能落在同一格（同一個 1 格高的區域，north 與 south 是同一格）
    pairs = [
        (a, b)
        for a in every_anchor
        for b in every_anchor
        if a != b and (anchor_cell(a) is None or anchor_cell(a) != anchor_cell(b))
    ]
    portals: list[PortalRule] = []
    if pairs:
        for name in portal_names:
            start, to = draw(st.sampled_from(pairs))
            portals.append(PortalRule(name, start, to))
    entities = [_retyped(draw, e, types) for e in entities]
    seed = draw(st.integers(-(2**63), 2**63 - 1))
    return qualified_slopes(
        Rules(
            seed,
            tuple(levels),
            kinds,
            tuple(areas),
            tuple(paths),
            tuple(entities),
            tuple(types),
            tuple(portals),
            VILLAGE_GROUNDS,
            VILLAGE_COLLISIONS,
        )
    )


def _retyped(draw: st.DrawFn, entity: EntityRule, types: list[EntityType]) -> EntityRule:
    """有時把 entity 換成 Part 或宣告的類型，帶上那個類型的欄位值。"""
    options: list[tuple[str, tuple[tuple[str, str], ...]]] = [
        ("PlayerSpawn", ()),
        ("Part", PART_FIELDS),
    ]
    options += [(t.name, t.fields) for t in types]
    kind, defined = draw(st.sampled_from(options))
    values = draw(part_fields()) if kind == "Part" else draw(field_values(defined))
    return replace(entity, type=kind, fields=values)


def _prefixed(r: Rules, prefix: str, level: str) -> Rules:
    """把一份單一 level 的規則搬到 `level`：元素名稱加上前綴，指向的名稱跟著改。"""

    def ref(name: str) -> str:
        return name if name.startswith("edge:") else prefix + name

    def anchor(at: str | tuple[int, int]) -> str | tuple[int, int]:
        return at if isinstance(at, tuple) else prefix + at

    areas = tuple(
        replace(a, name=prefix + a.name, near=ref(a.near) if a.near else None, level=level)
        for a in r.areas
    )
    paths = tuple(
        replace(p, name=prefix + p.name, start=ref(p.start), to=ref(p.to), level=level)
        for p in r.paths
    )
    entities = tuple(
        replace(e, name=prefix + e.name, at=anchor(e.at), level=level) for e in r.entities
    )
    return replace(r, areas=areas, paths=paths, entities=entities)


@st.composite
def multi_village_rules(draw: st.DrawFn, *, min_portals: int = 0) -> Rules:
    """2～3 個 level，每個 level 一套像村莊的元素；第二個以後的 level 靠近前一個或固定在格位上。

    有時再宣告一種 entity 類型，把一些 PlayerSpawn 換成它。
    """
    count = draw(st.integers(2, 3))
    parts = [draw(_raw_village_rules()) for _ in range(count)]
    levels: list[LevelRule] = []
    used: list[tuple[int, int]] = []
    for i, part in enumerate(parts):
        name, size = f"L{i}", part.level_size
        if i == 0:
            levels.append(LevelRule(name, size))
        elif draw(st.booleans()):
            side = draw(st.none() | st.sampled_from(SIDES))
            near = f"L{draw(st.integers(0, i - 1))}"
            levels.append(LevelRule(name, size, near=near, side=side, gap=draw(st.integers(0, 3))))
        else:
            # 固定格位彼此隔 100 格，village 最大 32 格，不會重疊；避開 L0 的 (0, 0)
            slots = [
                (x * 100, y * 100) for x in range(-2, 3) for y in range(-2, 3) if (x, y) != (0, 0)
            ]
            slot = draw(st.sampled_from([s for s in slots if s not in used]))
            used.append(slot)
            levels.append(LevelRule(name, size, at=slot))
    moved = [_prefixed(part, f"l{i}_", f"L{i}") for i, part in enumerate(parts)]
    types: list[EntityType] = []
    entities = [e for part in moved for e in part.entities]
    if draw(st.booleans()):
        sign = (("label", "String"), ("count", "Int"))
        types.append(EntityType("Sign", "#FFFFFF", (16, 16), sign))
        entities = [
            replace(e, type="Sign", fields=draw(field_values(sign))) if draw(st.booleans()) else e
            for e in entities
        ]
    # 傳送點（D-010）：兩個錨點，跨 level 或同一個 level 都有，區域與路徑的錨點都用
    areas = tuple(a for part in moved for a in part.areas)
    paths = tuple(p for part in moved for p in part.paths)
    anchors = [f"{a.name}.{s}" for a in areas for s in AREA_ANCHORS]
    anchors += [f"{p.name}.{s}" for p in paths for s in PATH_ANCHORS]
    portals: list[PortalRule] = []
    if len(anchors) >= 2:
        for i in range(draw(st.integers(min_portals, 2))):
            start, to = draw(
                st.lists(st.sampled_from(anchors), min_size=2, max_size=2, unique=True)
            )
            portals.append(PortalRule(f"door{i}", start, to))
    return qualified_slopes(
        Rules(
            parts[0].seed,
            tuple(levels),
            parts[0].kinds,
            areas,
            paths,
            tuple(entities),
            tuple(types),
            tuple(portals),
            parts[0].grounds,
            parts[0].collisions,
        )
    )


@st.composite
def placeable_multi_village_rules(draw: st.DrawFn, *, min_portals: int = 0) -> Rules:
    """排得下的多 level 規則：排不下就拿掉出錯的元素再試（見 placeable_village_rules）。"""
    r = draw(multi_village_rules(min_portals=min_portals))
    while True:
        try:
            layout(r)
        except SceneError as error:
            head = str(error).split("：", 1)[0]
            if head.startswith("level.") and head.endswith(".near"):
                # 靠近的那一邊被別的 level 佔滿：改成固定在遠處、彼此不重疊的格位
                stuck = head.split(".")[1]
                index = [lv.name for lv in r.levels].index(stuck)
                far = (1000 + 100 * index, 1000)
                levels = tuple(
                    LevelRule(lv.name, lv.size, at=far) if lv.name == stuck else lv
                    for lv in r.levels
                )
                r = replace(r, levels=levels)
                continue
            name = head.split(".", 1)[0]
            smaller = _without(r, name)
            if smaller == r:  # 訊息沒點名元素：不是排不下，是策略或配置器的錯
                raise
            r = smaller
            continue
        return qualified_slopes(r)
