"""GEN-1～GEN-4：照規則配置並生成場景地圖。"""

import dataclasses
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from ldtk_map_creator.core import template
from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.generate import element_iid, generate_scene, portal_iids
from ldtk_map_creator.core.layout import Layout, layout, placement_order
from ldtk_map_creator.core.model import EntityDef, Project
from ldtk_map_creator.core.rules import (
    SIDES,
    AreaRule,
    EntityRule,
    EntityType,
    Kind,
    PathRule,
    PortalRule,
    Rules,
    Side,
    load_rules,
)
from ldtk_map_creator.core.scene import SceneMap, add_level, load_scene, new_scene, save_scene
from tests.ldtk_schema import level_file_errors, load_json, project_errors
from tests.rule_strategies import (
    SIMPLE_GROUNDS,
    SIMPLE_KINDS,
    multi_village_rules,
    one_level_rules,
    placeable_multi_village_rules,
    placeable_village_rules,
    rules,
    village_rules,
)

FAST = settings(max_examples=150, deadline=None)
EXAMPLE = Path(__file__).parent / "data" / "village.rules.toml"
# 通用的規則偏稀疏；混進像村莊的規則，才常有靠近別人、被擠開的區域
spawn_rules = village_rules() | rules(entity_types=st.just("PlayerSpawn"), max_area=6)
# 大多排得下的規則：GEN-3 以外的 law 只看排得下的情況，篩掉太多會讓 hypothesis 放棄
fitting_rules = (
    placeable_village_rules()
    | placeable_multi_village_rules()
    | rules(entity_types=st.just("PlayerSpawn"), max_area=6)
)
STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))
# 區域在對象的哪一邊，就往反方向推才碰得到對象
DIRECTIONS = {"north": (0, 1), "south": (0, -1), "west": (1, 0), "east": (-1, 0)}


def _try_layout(r: Rules) -> Layout | None:
    try:
        return layout(r)
    except SceneError:
        return None


def _bbox(cells: frozenset[tuple[int, int]]) -> tuple[int, int, int, int]:
    xs = [x for x, _ in cells]
    ys = [y for _, y in cells]
    return min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def _files(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.mark.law("GEN-1")
@FAST
@given(fitting_rules, st.booleans())
def test_gen_1_same_rules_same_files(r: Rules, split: bool) -> None:
    assume(_try_layout(r) is not None)
    written: list[dict[str, bytes]] = []
    iids: list[list[str]] = []
    for _ in range(2):
        with tempfile.TemporaryDirectory() as tmp:
            scene, _ = generate_scene(r, Path(tmp) / "scene.ldtk", split=split)
            save_scene(scene)
            written.append(_files(Path(tmp)))
            layers = [ly for lv in scene.project["levels"] for ly in lv["layerInstances"] or []]
            iids.append(sorted(e["iid"] for ly in layers for e in ly["entityInstances"]))
    assert written[0] == written[1]
    expected = [element_iid(r.seed, e.name) for e in r.entities]
    expected += [iid for p in r.portals for iid in portal_iids(r.seed, p.name)]
    assert iids[0] == sorted(expected)


@pytest.mark.law("GEN-2")
@FAST
@given(fitting_rules)
def test_gen_2_layout_fits_and_matches_scene(r: Rules) -> None:
    placed = _try_layout(r)
    assume(placed is not None)
    assert placed is not None
    # 每個 level 各自一張格子圖：元素的格是它所在 level 的格座標
    owners: dict[str, dict[tuple[int, int], str]] = {name: {} for name in placed.levels}
    for name, cells in placed.cells.items():
        assert cells, name
        level = placed.level_of[name]
        assert level == r.level_of(next(e for e in (*r.areas, *r.paths) if e.name == name)).name
        _, _, width, height = placed.levels[level]
        for x, y in cells:
            assert 0 <= x < width and 0 <= y < height, (name, x, y)
            assert (x, y) not in owners[level], (name, owners[level].get((x, y)))
            owners[level][(x, y)] = name

    areas = {a.name: a for a in r.areas}
    for area in r.areas:
        x, y, w, h = _bbox(placed.cells[area.name])
        assert (w, h) == area.size
        assert len(placed.cells[area.name]) == w * h
        if area.near is None:
            continue
        owner = owners[placed.level_of[area.name]]
        # 跟其他區域至少隔一格；彼此靠近的兩個除外
        partners = {area.name, area.near} | {a.name for a in r.areas if a.near == area.name}
        ring = {(cx, cy) for cx in range(x - 1, x + w + 1) for cy in range(y - 1, y + h + 1)}
        for cell in ring:
            other = owner.get(cell)
            assert other is None or other in partners or other not in areas, (area.name, other)
        # 跟靠近的對象剛好隔 gap 格：往那一邊推 gap + 1 格就碰到對象，中間的 gap 格碰不到
        target = placed.cells[area.near]
        rect = {(cx, cy) for cx in range(x, x + w) for cy in range(y, y + h)}
        sides = [
            s
            for s, (dx, dy) in DIRECTIONS.items()
            if {(cx + dx * (area.gap + 1), cy + dy * (area.gap + 1)) for cx, cy in rect} & target
            and not any(
                (cx + dx * k, cy + dy * k) in target for cx, cy in rect for k in range(area.gap + 1)
            )
        ]
        assert sides and (area.side is None or area.side in sides), (area.name, sides)
        # 四周 gap 格內都碰不到對象（對象是 L 形時可能從別的方向更靠近）
        g = area.gap
        around = {(cx, cy) for cx in range(x - g, x + w + g) for cy in range(y - g, y + h + g)}
        assert not around & target, area.name

    def touches(cells: frozenset[tuple[int, int]], others: frozenset[tuple[int, int]]) -> bool:
        return any((x + dx, y + dy) in others for x, y in cells for dx, dy in STEPS)

    def distance(cell: tuple[int, int], name: str) -> int:
        return min(abs(cell[0] - x) + abs(cell[1] - y) for x, y in placed.cells[name])

    for area in r.areas:
        assert placed.anchors[f"{area.name}.center"] in placed.cells[area.name]
        # 四邊的錨點在區域自己的格裡、貼著那一邊（D-010）
        bx, by, bw, bh = _bbox(placed.cells[area.name])
        edge = {
            "north": (1, by),
            "south": (1, by + bh - 1),
            "west": (0, bx),
            "east": (0, bx + bw - 1),
        }
        for side, (axis, line) in edge.items():
            anchor = placed.anchors[f"{area.name}.{side}"]
            assert anchor in placed.cells[area.name] and anchor[axis] == line, (area.name, side)
    for path in r.paths:
        cells = placed.cells[path.name]
        _, _, width, height = placed.levels[placed.level_of[path.name]]
        start, end = placed.anchors[f"{path.name}.start"], placed.anchors[f"{path.name}.end"]
        assert start in cells and end in cells, path.name
        # start 靠起點那頭，end 靠終點那頭
        assert distance(start, path.start) <= distance(end, path.start), path.name
        if not path.to.startswith("edge:"):
            assert distance(end, path.to) <= distance(start, path.to), path.name
        # 路要接得上起點，另一頭接得上終點或通到那一邊
        assert touches(cells, placed.cells[path.start]), path.name
        if path.to.startswith("edge:"):
            on_edge = {
                "edge:north": end[1] == 0,
                "edge:south": end[1] == height - 1,
                "edge:west": end[0] == 0,
                "edge:east": end[0] == width - 1,
            }
            assert on_edge[path.to], (path.name, end)
        else:
            assert touches(cells, placed.cells[path.to]), path.name

    for entity in r.entities:
        expected = entity.at if isinstance(entity.at, tuple) else placed.anchors[entity.at]
        assert placed.entities[entity.name] == expected
        assert placed.level_of[entity.name] == r.level_of(entity).name
    spots = [(placed.level_of[n], c) for n, c in placed.entities.items()]
    assert len(set(spots)) == len(spots)  # 同一個 level 裡不擺在同一格

    with tempfile.TemporaryDirectory() as tmp:
        scene, _ = generate_scene(r, Path(tmp) / "scene.ldtk")
        save_scene(scene)
        loaded = load_scene(Path(tmp) / "scene.ldtk")
    kinds = {k.name: k for k in r.kinds}
    kind = {e.name: kinds[e.kind] for e in (*r.areas, *r.paths)}
    ground = {v.name: v.value for v in r.grounds}
    collision = {v.name: v.value for v in r.collisions}
    # 每一層每格的值：佔它的元素的圖格種類寫進那一層的值，沒寫或沒被佔的是 0（D-011）
    wanted: dict[str, Callable[[Kind], int]] = {
        "Ground": lambda k: ground[k.ground] if k.ground else 0,
        "Collision": lambda k: collision[k.collision] if k.collision else 0,
        "Height": lambda k: k.height,
        "Slope": lambda k: 1 if k.slope else 0,
    }
    top = max((k.height for k in r.kinds), default=0)
    # 各層的值定義：Ground、Collision 照規則檔，Height 到用到的最大階數，Slope 一個值
    defined = {
        d["identifier"]: [(v["value"], v["identifier"]) for v in d["intGridValues"]]
        for d in loaded.project["defs"]["layers"]
    }
    assert defined["Ground"] == [(v.value, v.name) for v in r.grounds]
    assert defined["Collision"] == [(v.value, v.name) for v in r.collisions]
    assert defined["Height"] == [(n, f"h{n}") for n in range(1, top + 1)]
    assert defined["Slope"] == [(1, "slope")]
    assert [d["identifier"] for d in loaded.project["defs"]["layers"]] == [
        "Entities",
        "Collision",
        "Slope",
        "Height",
        "Ground",
    ]
    by_iid = {element_iid(r.seed, e.name): e.name for e in r.entities}
    portal_ends = {iid for p in r.portals for iid in portal_iids(r.seed, p.name)}
    got: dict[str, tuple[int, ...]] = {}
    grid = loaded.project["defaultGridSize"]
    for level_name, (lx, ly, width, height) in placed.levels.items():
        level = loaded.level(level_name)
        assert (level["worldX"], level["worldY"]) == (lx * grid, ly * grid)
        owner = owners[level_name]
        layers = level["layerInstances"] or []
        for layer_name, pick in wanted.items():
            intgrid = next(ly for ly in layers if ly["__identifier"] == layer_name)
            expected_csv = [
                pick(kind[owner[(cx, cy)]]) if (cx, cy) in owner else 0
                for cy in range(height)
                for cx in range(width)
            ]
            assert intgrid["intGridCsv"] == expected_csv, (level_name, layer_name)
        for layer in layers:
            for e in layer["entityInstances"]:
                if e["iid"] in portal_ends:
                    continue  # 傳送點另外由 GEN-8 檢查
                got[by_iid[e["iid"]]] = tuple(e["__grid"])
                assert placed.level_of[by_iid[e["iid"]]] == level_name
    assert got == placed.entities
    # 宣告的 entity 類型變成定義：名稱、顏色、大小、欄位都照規則
    defs = {d["identifier"]: d for d in loaded.project["defs"]["entities"]}
    for declared in r.entity_types:
        made = defs[declared.name]
        assert (made["color"], (made["width"], made["height"])) == (declared.color, declared.size)
        assert [(f["identifier"], f["__type"]) for f in made["fieldDefs"]] == list(declared.fields)


@pytest.mark.law("GEN-3")
@FAST
@given(spawn_rules)
def test_gen_3_failure_names_an_element(r: Rules) -> None:
    try:
        layout(r)
    except SceneError as error:
        names = {e.name for e in (*r.areas, *r.paths, *r.entities)}
        head = str(error).split("：", 1)[0]
        element, _, field = head.partition(".")
        assert element in names and field in ("at", "near", "to"), str(error)


@st.composite
def changed_area(draw: st.DrawFn) -> tuple[Rules, Rules, str]:
    """改一個區域的一個欄位：位置、大小、靠近的一邊或間隔。"""
    r = draw(village_rules() | multi_village_rules())
    assume(r.areas)
    x = draw(st.sampled_from(r.areas))
    width, height = r.level_of(x).size
    w, h = x.size
    if x.at is not None:
        field = draw(st.sampled_from(["at", "size"]))
    else:
        field = draw(st.sampled_from(["side", "gap", "size"]))
    if field == "at":
        new = dataclasses.replace(
            x, at=(draw(st.integers(0, width - w)), draw(st.integers(0, height - h)))
        )
    elif field == "size":
        x0, y0 = x.at if isinstance(x.at, tuple) else (0, 0)
        size = (draw(st.integers(1, width - x0)), draw(st.integers(1, height - y0)))
        new = dataclasses.replace(x, size=size)
    elif field == "side":
        new = dataclasses.replace(x, side=draw(st.none() | st.sampled_from(SIDES)))
    else:
        new = dataclasses.replace(x, gap=draw(st.integers(0, 5)))
    areas = tuple(new if a is x else a for a in r.areas)
    return r, dataclasses.replace(r, areas=areas), x.name


@pytest.mark.law("GEN-4")
# 新舊兩次都要排得成功才比得了，篩掉的例子本來就多
@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
@given(changed_area())
def test_gen_4_changing_an_area_only_moves_what_it_touches(
    case: tuple[Rules, Rules, str],
) -> None:
    old_rules, new_rules, moved_name = case
    old, new = _try_layout(old_rules), _try_layout(new_rules)
    assume(old is not None and new is not None)
    assert old is not None and new is not None
    moved = {moved_name}
    for element in placement_order(old_rules):
        if element.name == moved_name:
            continue
        refs = {element.near} if isinstance(element, AreaRule) else {element.start, element.to}
        touched = refs | old.pushed_by[element.name] | new.pushed_by[element.name]
        if touched & moved:
            if old.cells[element.name] != new.cells[element.name]:
                moved.add(element.name)
            continue
        assert old.cells[element.name] == new.cells[element.name], (element.name, moved)
    for entity in old_rules.entities:
        if isinstance(entity.at, str) and entity.at.split(".")[0] in moved:
            continue
        assert old.entities[entity.name] == new.entities[entity.name], entity.name


@pytest.mark.law("FILE-1")
@settings(max_examples=40, deadline=None)
@given(fitting_rules, st.booleans())
def test_file_1_generated_scene_matches_schema(r: Rules, split: bool) -> None:
    """FILE-1 的定義域擴到生成的場景地圖：生成會改 IntGrid 定義與 iid，範本測不到。"""
    assume(_try_layout(r) is not None)
    with tempfile.TemporaryDirectory() as tmp:
        scene, _ = generate_scene(r, Path(tmp) / "scene.ldtk", split=split)
        save_scene(scene)
        for path in Path(tmp).rglob("*.ldtk*"):
            data = load_json(path)
            errors = project_errors(data) if path.suffix == ".ldtk" else level_file_errors(data)
            assert errors == [], path.name


def test_example_village() -> None:
    """典型：PlayerSpawn 在主路通到南邊的盡頭，house_2 在廣場東邊隔 3 格。"""
    r = load_rules(EXAMPLE)
    placed = layout(r)
    width, height = r.level_size
    assert placed.entities["spawn"] == placed.anchors["main_road.end"]
    assert placed.anchors["main_road.end"][1] == height - 1
    px, _, pw, _ = _bbox(placed.cells["plaza"])
    hx, _, _, _ = _bbox(placed.cells["house_2"])
    assert hx - (px + pw) == 3
    assert width == 40


def test_example_no_room_names_the_element(tmp_path: Path) -> None:
    """排不下時的訊息：指出元素、靠近誰、哪一邊。"""
    path = tmp_path / "tight.rules.toml"
    path.write_text(
        """
seed = 1
[level]
name = "L"
size = [10, 10]
[grounds]
g = { value = 1, color = "#000000" }
[kinds]
k = { ground = "g" }
[[area]]
name = "plaza"
kind = "k"
size = [8, 8]
at = "center"
[[area]]
name = "house"
kind = "k"
size = [5, 4]
near = "plaza"
side = "east"
gap = 3
""",
        encoding="utf-8",
    )
    with pytest.raises(SceneError, match=r"^house\.near：在 plaza 的東邊排不下 5x4"):
        layout(load_rules(path))


def test_example_edge_path_from_l_shaped_path() -> None:
    """曾經壞過的地方：從 L 形路徑通到邊的路，要從路實際佔的格接出去，不是從外框。"""
    for seed in range(10):  # L 形先橫或先直由亂數決定，兩種都要走到
        r = one_level_rules(
            seed,
            "L",
            (20, 20),
            SIMPLE_KINDS,
            (
                AreaRule("a", "k", (2, 2), at=(2, 2)),
                AreaRule("b", "k", (2, 2), at=(14, 14)),
            ),
            (PathRule("link", "k", "a", "b"), PathRule("branch", "k", "link", "edge:east")),
            (),
        )
        placed = layout(r)
        branch, link = placed.cells["branch"], placed.cells["link"]
        assert any((x - 1, y) in link for x, y in branch), seed


def _two_areas(seed: int, a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> Rules:
    return one_level_rules(
        seed,
        "L",
        (20, 20),
        SIMPLE_KINDS,
        (
            AreaRule("a", "k", (a[2], a[3]), at=(a[0], a[1])),
            AreaRule("b", "k", (b[2], b[3]), at=(b[0], b[1])),
        ),
        (PathRule("link", "k", "a", "b"),),
        (),
        grounds=SIMPLE_GROUNDS,
    )


def test_example_area_near_long_l_shaped_path() -> None:
    """曾經壞過的地方：靠近 L 形路徑的區域要貼著路排，不是貼著路的外框（外框幾乎蓋滿 level）。"""
    for seed in range(10):
        base = _two_areas(seed, (0, 0, 1, 1), (19, 19, 1, 1))
        house = AreaRule("house", "k", (2, 2), near="link", gap=1)
        placed = layout(dataclasses.replace(base, areas=(*base.areas, house)))
        x, y, _, _ = _bbox(placed.cells["house"])
        road = placed.cells["link"]
        assert min(abs(x - rx) + abs(y - ry) for rx, ry in road) <= 4, seed


def test_example_link_path_tries_the_other_shape() -> None:
    """曾經壞過的地方：一種 L 形整條落在兩端裡時，要改試另一種，不看亂數先抽到哪一種。"""
    for seed in range(10):
        placed = layout(_two_areas(seed, (0, 0, 2, 2), (2, 1, 2, 4)))
        assert placed.cells["link"], seed


def test_example_unparsed_cycle_is_scene_error() -> None:
    """沒經過規則檔檢查、直接組出來的成環規則，也丟 SceneError，不丟 StopIteration。"""
    r = one_level_rules(
        1,
        "L",
        (10, 10),
        SIMPLE_KINDS,
        (AreaRule("x", "k", (1, 1), near="y"), AreaRule("y", "k", (1, 1), near="x")),
        (),
        (),
        grounds=SIMPLE_GROUNDS,
    )
    with pytest.raises(SceneError, match=r"^x\.near"):
        layout(r)


@pytest.mark.parametrize("behavior", ["MoveLastOne", "DiscardOldOnes", "PreventAdding"])
def test_example_entity_max_count(
    behavior: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """entity 定義有數量上限時，擺不下的報錯，不悄悄挪走或丟掉先前擺的。"""
    original = template.project

    def limited() -> Project:
        project = original()
        spawn = project["defs"]["entities"][0]
        spawn["maxCount"], spawn["limitBehavior"] = 1, behavior
        return project

    monkeypatch.setattr(template, "project", limited)
    r = load_rules(EXAMPLE)
    second = EntityRule("spawn_2", "PlayerSpawn", "plaza.center")
    r = dataclasses.replace(r, entities=(*r.entities, second))
    with pytest.raises(SceneError, match=r"^spawn_2\.type"):
        generate_scene(r, tmp_path / "scene.ldtk")


@pytest.mark.parametrize("side", SIDES)
def test_example_every_side_has_room(side: Side) -> None:
    """典型：廣場四周都空著時，指定哪一邊就排在哪一邊。"""
    base = _two_areas(0, (9, 9, 2, 2), (0, 0, 1, 1))
    house = AreaRule("house", "k", (3, 2), near="a", side=side, gap=2)
    placed = layout(dataclasses.replace(base, areas=(*base.areas, house), paths=()))
    hx, hy, hw, hh = _bbox(placed.cells["house"])
    gaps = {"north": 9 - (hy + hh), "south": hy - 11, "west": 9 - (hx + hw), "east": hx - 11}
    assert gaps[side] == 2


def test_example_wide_link_path_stays_between_its_ends() -> None:
    """曾經壞過的地方：寬的 L 形路不繞到兩端的背後（a 在 x=2、b 在 x=12）。"""
    placed = layout(
        dataclasses.replace(
            _two_areas(0, (2, 2, 1, 1), (12, 2, 1, 1)), paths=(PathRule("link", "k", "a", "b", 3),)
        )
    )
    xs = {x for x, _ in placed.cells["link"]}
    assert min(xs) >= 2 and max(xs) <= 12


def test_example_near_l_shaped_path_keeps_gap_on_every_side() -> None:
    """曾經壞過的地方：靠近 L 形路的區域，路的另一段也不能比 gap 更近。

    路先橫後直（y=10 橫過去、x=10 豎下來）時，豎段西邊緊貼著橫段下方的位置要排除。
    """
    # 這幾個 seed 在修正前會把區域貼到橫段下方
    for seed in (36, 37, 71, 76, 85, 86, 95, 124, 126, 127):
        base = _two_areas(seed, (0, 10, 1, 1), (10, 19, 1, 1))
        house = AreaRule("house", "k", (2, 2), near="link", side="west", gap=2)
        try:
            placed = layout(dataclasses.replace(base, areas=(*base.areas, house)))
        except SceneError:
            continue  # 修正後這個位置排不進去也算對：重點是不能貼著路
        x, y, _, _ = _bbox(placed.cells["house"])
        around = {(cx, cy) for cx in range(x - 2, x + 4) for cy in range(y - 2, y + 4)}
        assert not around & placed.cells["link"], seed


@pytest.mark.parametrize(
    ("change", "where"),
    [
        ("entity_anchor", r"^ghost\.at"),
        ("no_position", r"^lost\.at"),
        ("missing_target", r"^lost\.near"),
    ],
)
def test_example_unparsed_rules_raise_scene_error(change: str, where: str) -> None:
    """沒經過規則檔檢查、直接組出來的規則寫錯，也丟 SceneError，訊息指出元素與欄位。"""
    base = _two_areas(0, (0, 0, 1, 1), (5, 5, 1, 1))
    if change == "entity_anchor":
        r = dataclasses.replace(
            base, entities=(EntityRule("ghost", "PlayerSpawn", "nowhere.center"),)
        )
    elif change == "no_position":
        r = dataclasses.replace(base, areas=(*base.areas, AreaRule("lost", "k", (1, 1))))
    else:
        lost = AreaRule("lost", "k", (1, 1), near="nowhere")
        r = dataclasses.replace(base, areas=(*base.areas, lost))
    with pytest.raises(SceneError, match=where):
        layout(r)


def test_example_village_with_house_interior(tmp_path: Path) -> None:
    """典型（D-009、D-010）：兩個 level、宣告的 entity 類型；House1 在村莊東邊隔 2 格、置中。"""
    r = load_rules(EXAMPLE.with_name("village_house.rules.toml"))
    r = dataclasses.replace(r, portals=())  # 傳送點是 #37
    scene, placed = generate_scene(r, tmp_path / "scene.ldtk")
    vx, vy, vw, vh = placed.levels["Village"]
    hx, hy, _, hh = placed.levels["House1"]
    assert (vx, vy) == (0, 0) and hx == vx + vw + 2
    assert hy == vy + (vh - hh) // 2
    house = scene.level("House1")
    assert (house["worldX"], house["worldY"]) == (hx * 16, hy * 16)
    portal = next(d for d in scene.project["defs"]["entities"] if d["identifier"] == "Portal")
    assert [f["identifier"] for f in portal["fieldDefs"]] == ["target"]
    assert placed.level_of["room"] == "House1"


def test_example_portal_links_village_and_house(tmp_path: Path) -> None:
    """典型（D-010）：house_1 門口與房間南邊各一個 Portal，target 互指、跨 level。"""
    r = load_rules(EXAMPLE.with_name("village_house.rules.toml"))
    scene, placed = generate_scene(r, tmp_path / "scene.ldtk")
    save_scene(scene)
    loaded = load_scene(tmp_path / "scene.ldtk")
    found = {
        e["iid"]: (lv, e)
        for lv in loaded.project["levels"]
        for ly in lv["layerInstances"] or []
        for e in ly["entityInstances"]
        if e["__identifier"] == "Portal"
    }
    a, b = portal_iids(r.seed, "house_1_door")
    assert set(found) == {a, b}
    for me, other in ((a, b), (b, a)):
        _, entity = found[me]
        ref = next(f for f in entity["fieldInstances"] if f["__identifier"] == "target")
        value = cast(dict[str, object], ref["__value"])
        assert value["entityIid"] == other
        assert value["levelIid"] == found[other][0]["iid"]
    (fl, fc), (tl, tc) = placed.portals["house_1_door"]
    assert (fl, tl) == ("Village", "House1")
    assert tuple(found[a][1]["__grid"]) == fc and tuple(found[b][1]["__grid"]) == tc


def test_example_single_level_at_sets_world_position(tmp_path: Path) -> None:
    """只有一個 level 也照 `at` 擺在世界上（D-009，格為單位）。"""
    path = tmp_path / "x.rules.toml"
    text = EXAMPLE.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            '[level]\nname = "Village"\nsize = [40, 30]  # 格',
            '[[level]]\nname = "Village"\nsize = [40, 30]\nat = [3, -2]',
        ),
        encoding="utf-8",
    )
    scene, _ = generate_scene(load_rules(path), tmp_path / "scene.ldtk")
    level = scene.level("Village")
    assert (level["worldX"], level["worldY"]) == (3 * 16, -2 * 16)


def test_example_layout_ignores_declared_types() -> None:
    """配置器只算位置：宣告了 entity 類型照樣排得出來（不支援的生成在 generate_scene 擋）。"""
    r = load_rules(EXAMPLE)
    r = dataclasses.replace(r, entity_types=(EntityType("Sign"),))
    assert layout(r).cells


@pytest.mark.law("GEN-7")
@FAST
@given(placeable_multi_village_rules())
def test_gen_7_world_layout(r: Rules) -> None:
    placed = layout(r)
    rects = placed.levels
    assert list(rects) == [lv.name for lv in r.levels]
    names = list(rects)
    for i, a in enumerate(names):  # level 彼此不重疊
        ax, ay, aw, ah = rects[a]
        assert (aw, ah) == r.levels[i].size
        for b in names[i + 1 :]:
            bx, by, bw, bh = rects[b]
            assert not (ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah), (a, b)
    for level in r.levels:
        x, y, w, h = rects[level.name]
        if level.near is None:
            assert (x, y) == (level.at or (0, 0))
            continue
        # 貼著對象那一邊、剛好隔 gap 格，而且兩邊有重疊的地方（不是斜對角）
        tx, ty, tw, th = rects[level.near]
        gaps = {
            "north": (ty - (y + h), x < tx + tw and tx < x + w),
            "south": (y - (ty + th), x < tx + tw and tx < x + w),
            "west": (tx - (x + w), y < ty + th and ty < y + h),
            "east": (x - (tx + tw), y < ty + th and ty < y + h),
        }
        sides = [s for s, (g, facing) in gaps.items() if g == level.gap and facing]
        assert sides and (level.side is None or level.side in sides), (level.name, gaps)


def test_example_other_levels_stay_when_one_changes() -> None:
    """改一個 level 裡的區域，別的 level 的元素完全不動。

    每個元素用自己的亂數，各 level 分開排。
    """
    r = load_rules(EXAMPLE.with_name("village_house.rules.toml"))
    r = dataclasses.replace(r, portals=())
    before = layout(r)
    areas = tuple(dataclasses.replace(a, size=(4, 4)) if a.name == "room" else a for a in r.areas)
    after = layout(dataclasses.replace(r, areas=areas))
    for name in ("plaza", "house_1"):
        assert before.cells[name] == after.cells[name]
    assert before.levels == after.levels


def test_example_entity_type_errors_name_the_type(tmp_path: Path) -> None:
    """直接組出來、沒經過規則檔檢查的 entity 類型寫錯：訊息開頭是 entity_types.<名稱>。"""
    r = dataclasses.replace(load_rules(EXAMPLE), entity_types=(EntityType("PlayerSpawn"),))
    with pytest.raises(SceneError, match=r"^entity_types\.PlayerSpawn："):
        generate_scene(r, tmp_path / "scene.ldtk")


def test_example_layout_refuses_anchor_in_other_level() -> None:
    """直接組出來的規則把 entity 擺在別的 level 的錨點：配置器也擋，不拿別的 level 的座標。"""
    r = load_rules(EXAMPLE.with_name("village_house.rules.toml"))
    stray = EntityRule("stray", "PlayerSpawn", "plaza.center", level="House1")
    r = dataclasses.replace(r, portals=(), entities=(stray,))
    with pytest.raises(SceneError, match=r"^stray\.at："):
        layout(r)


@pytest.mark.law("GEN-8")
@FAST
@given(placeable_multi_village_rules(min_portals=1))
def test_gen_8_portals_link_both_ends(r: Rules) -> None:
    assume(r.portals)
    with tempfile.TemporaryDirectory() as tmp:
        scene, placed = generate_scene(r, Path(tmp) / "scene.ldtk")
        save_scene(scene)
        loaded = load_scene(Path(tmp) / "scene.ldtk")
    where: dict[str, tuple[str, tuple[int, ...], dict[str, Any]]] = {}
    spots: list[tuple[str, tuple[int, ...]]] = []
    for level in loaded.project["levels"]:
        for layer in level["layerInstances"] or []:
            for e in layer["entityInstances"]:
                where[e["iid"]] = (level["identifier"], tuple(e["__grid"]), dict(e))
                spots.append((level["identifier"], tuple(e["__grid"])))
    assert len(set(spots)) == len(spots)  # 同一個 level 裡沒有兩個 entity 在同一格
    level_iids = {lv["identifier"]: lv["iid"] for lv in loaded.project["levels"]}
    areas = {a.name: a for a in r.areas}

    paths = {p.name: p for p in r.paths}

    def anchor_spot(anchor: str) -> tuple[str, tuple[int, int]]:
        """錨點在哪個 level、哪一格；區域的錨點獨立於配置器另算（區域的格加上錨點的算式）。"""
        owner, _, which = anchor.partition(".")
        if owner in paths:  # 路徑的錨點要排了才知道（GEN-2 檢查它在路上、靠對的那頭）
            return r.level_of(paths[owner]).name, placed.anchors[anchor]
        x, y, w, h = _bbox(placed.cells[owner])
        dx = {"west": 0, "east": w - 1}.get(which, w // 2)
        dy = {"north": 0, "south": h - 1}.get(which, h // 2)
        return r.level_of(areas[owner]).name, (x + dx, y + dy)

    for portal in r.portals:
        start, end = portal_iids(r.seed, portal.name)
        (from_level, from_cell), (to_level, to_cell) = placed.portals[portal.name]
        assert (from_level, from_cell) == anchor_spot(portal.start)
        assert (to_level, to_cell) == anchor_spot(portal.to)
        assert where[start][:2] == (from_level, from_cell)  # 擺在錨點上
        assert where[end][:2] == (to_level, to_cell)
        assert (from_level, from_cell) != (to_level, to_cell)  # 兩端不在同一格
        for me, other in ((start, end), (end, start)):  # EntityRef 互指，可以跨 level
            entity = where[me][2]
            assert entity["__identifier"] == portal.type
            ref = next(f for f in entity["fieldInstances"] if f["__type"] == "EntityRef")
            value = cast(dict[str, object], ref["__value"])
            assert value["entityIid"] == other
            assert value["levelIid"] == level_iids[where[other][0]]


def test_example_portal_on_one_cell_road_is_refused() -> None:
    """#37 驗收：一格長的路 start 與 end 是同一格；傳送點兩端在同一格要報錯（排了才知道）。"""
    r = one_level_rules(
        0,
        "L",
        (6, 4),
        SIMPLE_KINDS,
        (AreaRule("house", "k", (2, 2), at=(2, 1)),),
        (PathRule("road", "k", "house", "edge:north"),),
        (),
        grounds=SIMPLE_GROUNDS,
    )
    portal = PortalRule("p", "Portal", "road.start", "road.end")
    kind = EntityType("Portal", fields=(("target", "EntityRef"),))
    r = dataclasses.replace(r, entity_types=(kind,), portals=(portal,))
    placed_road = layout(dataclasses.replace(r, portals=())).anchors
    assert placed_road["road.start"] == placed_road["road.end"]
    with pytest.raises(SceneError, match=r"^p\.to："):
        layout(r)


def test_example_portal_hits_max_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """傳送點類型有數量上限時，第二端擺不下要說清楚，不悄悄挪走第一端。"""
    original = template.entity_def

    def limited() -> EntityDef:
        found = original()
        found["maxCount"], found["limitBehavior"] = 1, "MoveLastOne"
        return found

    monkeypatch.setattr(template, "entity_def", limited)
    r = load_rules(EXAMPLE.with_name("village_house.rules.toml"))
    r = dataclasses.replace(r, levels=(r.levels[0],), areas=r.areas[:2])
    door = PortalRule("door", "Portal", "house_1.south", "plaza.north")
    r = dataclasses.replace(r, portals=(door,))
    with pytest.raises(SceneError, match=r"^door\.to：.*上限"):
        generate_scene(r, tmp_path / "scene.ldtk")


def test_example_portal_type_checked_before_placing(tmp_path: Path) -> None:
    """直接組出來的傳送點類型不對（例如 PlayerSpawn）：擺任何東西之前就報錯。"""
    r = load_rules(EXAMPLE)
    door = PortalRule("door", "PlayerSpawn", "plaza.north", "plaza.south")
    with pytest.raises(SceneError, match=r"^door\.type："):
        generate_scene(dataclasses.replace(r, portals=(door,)), tmp_path / "scene.ldtk")


def test_example_portal_across_levels_hits_world_max_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """類型的數量上限是整個世界時，第二端會把另一個 level 的第一端挪走：要擋下來說清楚。"""
    original = template.entity_def

    def limited() -> EntityDef:
        found = original()
        found["maxCount"], found["limitBehavior"], found["limitScope"] = (
            1,
            "MoveLastOne",
            "PerWorld",
        )
        return found

    monkeypatch.setattr(template, "entity_def", limited)
    r = load_rules(EXAMPLE.with_name("village_house.rules.toml"))
    with pytest.raises(SceneError, match=r"^house_1_door\.to：.*上限"):
        generate_scene(r, tmp_path / "scene.ldtk")


def _convention_problems(scene: SceneMap) -> list[str]:
    """場景地圖有沒有照 D-011 的五層與三種 entity 定義；獨立於 core 另寫一次名稱。"""
    problems: list[str] = []
    layers = [(d["identifier"], d["type"]) for d in scene.project["defs"]["layers"]]
    wanted = [
        ("Entities", "Entities"),
        ("Collision", "IntGrid"),
        ("Slope", "IntGrid"),
        ("Height", "IntGrid"),
        ("Ground", "IntGrid"),
    ]
    if layers != wanted:
        problems.append(f"圖層定義 {layers}")
    defs = {d["identifier"]: d for d in scene.project["defs"]["entities"]}
    shapes: dict[str, tuple[list[str], list[tuple[str, str]]]] = {
        "PlayerSpawn": (["player"], []),
        "Teleport": ([], [("targetPort", "EntityRef")]),
        "Part": ([], [("part", "String"), ("yaw", "Float")]),
    }
    for name, (tags, fields) in shapes.items():
        found = defs.get(name)
        if found is None:
            problems.append(f"沒有 {name}")
        elif (found["tags"], [(f["identifier"], f["__type"]) for f in found["fieldDefs"]]) != (
            tags,
            fields,
        ):
            problems.append(f"{name} 的 tags 或欄位不對")
    for name, found in defs.items():
        if name not in shapes and found["tags"]:
            problems.append(f"{name} 不該帶 tags")
    for level in scene.project["levels"]:
        placed = [ly["__identifier"] for ly in level["layerInstances"] or []]
        if placed != [name for name, _ in wanted]:
            problems.append(f"level {level['identifier']} 的圖層實例 {placed}")
    return problems


@pytest.mark.law("SCENE-8")
@settings(max_examples=40, deadline=None)
@given(fitting_rules, st.booleans(), st.tuples(st.integers(1, 8), st.integers(1, 8)))
def test_scene_8_new_and_generated_scenes_follow_the_convention(
    r: Rules, split: bool, size: tuple[int, int]
) -> None:
    assume(_try_layout(r) is not None)
    with tempfile.TemporaryDirectory() as tmp:
        blank = new_scene(Path(tmp) / "blank.ldtk", split=split)
        add_level(blank, "Extra", width_cells=size[0], height_cells=size[1], world_x=4096)
        assert _convention_problems(blank) == []
        scene, _ = generate_scene(r, Path(tmp) / "scene.ldtk", split=split)
        assert _convention_problems(scene) == []


def test_example_terrain_village() -> None:
    """典型（D-011）：小丘兩階、小丘南邊的斜坡一階加斜坡、實心的房子、池塘只有實心沒有地面。"""
    r = load_rules(EXAMPLE.with_name("village_terrain.rules.toml"))
    with tempfile.TemporaryDirectory() as tmp:
        scene, placed = generate_scene(r, Path(tmp) / "scene.ldtk")
    level = scene.level("Village")
    layers = {ly["__identifier"]: ly["intGridCsv"] for ly in level["layerInstances"] or []}
    width = r.level_size[0]

    def at(layer: str, cell: tuple[int, int]) -> int:
        return layers[layer][cell[1] * width + cell[0]]

    # 值：grass 1、dirt 2；wall 1、water 2
    for cell in placed.cells["hill"]:
        assert [at(n, cell) for n in ("Ground", "Collision", "Height", "Slope")] == [1, 0, 2, 0]
    for cell in placed.cells["ramp"]:
        assert [at(n, cell) for n in ("Ground", "Collision", "Height", "Slope")] == [1, 0, 1, 1]
    for cell in placed.cells["house_1"]:
        assert [at(n, cell) for n in ("Ground", "Collision", "Height", "Slope")] == [2, 1, 0, 0]
    for cell in placed.cells["pond"]:
        assert [at(n, cell) for n in ("Ground", "Collision", "Height", "Slope")] == [0, 2, 0, 0]
    painted: set[tuple[int, int]] = set()
    for cells in placed.cells.values():
        painted |= cells
    for name, csv in layers.items():
        for index, value in enumerate(csv):
            cell = (index % width, index // width)
            assert cell in painted or value == 0, (name, cell)
