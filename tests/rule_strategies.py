"""產生合法規則檔的 hypothesis 策略，以及把規則資料轉回 TOML 讀出的樣子。"""

from typing import Any

from hypothesis import strategies as st

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

names = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,8}", fullmatch=True)


@st.composite
def rules(draw: st.DrawFn) -> Rules:
    """合法的規則：每個元素只指向排在它前面的區域或路徑，所以一定不成環。"""
    width, height = draw(st.integers(1, 40)), draw(st.integers(1, 40))
    kind_names = draw(st.lists(names, min_size=1, max_size=4, unique=True))
    values = draw(
        st.lists(
            st.integers(1, 99), min_size=len(kind_names), max_size=len(kind_names), unique=True
        )
    )
    colors = st.integers(0, 0xFFFFFF).map(lambda c: f"#{c:06X}")
    kinds = tuple(Kind(n, v, draw(colors)) for n, v in zip(kind_names, values, strict=True))
    element_names = draw(st.lists(names, max_size=10, unique=True))

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
            w, h = draw(st.integers(1, width)), draw(st.integers(1, height))
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
            width_cells = draw(st.integers(1, max(width, height)))
            paths.append(PathRule(name, draw(kind), start, to, width_cells))
            targets.append(name)
            anchors += [f"{name}.{a}" for a in PATH_ANCHORS]
        else:
            cell = st.tuples(st.integers(0, width - 1), st.integers(0, height - 1))
            at = draw(st.sampled_from(anchors) | cell if anchors else cell)
            entities.append(EntityRule(name, draw(names), at))
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
