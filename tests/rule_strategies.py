"""產生合法規則檔的 hypothesis 策略，以及把規則資料轉回 TOML 讀出的樣子。"""

from dataclasses import replace
from typing import Any

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
    Kind,
    PathRule,
    Rules,
)


def width_limit(to: str, level_size: tuple[int, int]) -> int:
    """路徑最寬能多寬，獨立於 core 另寫一次：南北向不能比 level 寬，東西向不能比 level 高。"""
    width, height = level_size
    if to in ("edge:north", "edge:south"):
        return width
    if to in ("edge:east", "edge:west"):
        return height
    return max(width, height)


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
    level = draw(names)
    return Rules(seed, level, (width, height), kinds, tuple(areas), tuple(paths), tuple(entities))


def to_toml_data(r: Rules) -> dict[str, Any]:
    """規則資料寫成 TOML 再用 tomllib 讀出來的樣子；選填欄位是預設值時不寫。"""
    data: dict[str, Any] = {
        "seed": r.seed,
        "level": {"name": r.level_name, "size": list(r.level_size)},
        "kinds": {k.name: {"value": k.value, "color": k.color} for k in r.kinds},
    }
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
        areas.append(area)
    paths: list[dict[str, Any]] = []
    for p in r.paths:
        path: dict[str, Any] = {"name": p.name, "kind": p.kind, "from": p.start, "to": p.to}
        if p.width != 1:
            path["width"] = p.width
        paths.append(path)
    entities = [
        {"name": e.name, "type": e.type, "at": e.at if isinstance(e.at, str) else list(e.at)}
        for e in r.entities
    ]
    for key, items in (("area", areas), ("path", paths), ("entity", entities)):
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
    return Rules(
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
            r = _without(r, name)
            continue
        return r
