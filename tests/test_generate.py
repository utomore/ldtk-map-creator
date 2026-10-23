"""GEN-1～GEN-4：照規則配置並生成場景地圖。"""

import dataclasses
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from ldtk_map_creator.core import template
from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.generate import element_iid, generate_scene
from ldtk_map_creator.core.layout import Layout, layout, placement_order
from ldtk_map_creator.core.model import Project
from ldtk_map_creator.core.rules import (
    SIDES,
    AreaRule,
    EntityRule,
    Kind,
    PathRule,
    Rules,
    Side,
    load_rules,
)
from ldtk_map_creator.core.scene import load_scene, save_scene
from tests.ldtk_schema import level_file_errors, load_json, project_errors
from tests.rule_strategies import one_level_rules, placeable_village_rules, rules, village_rules

FAST = settings(max_examples=150, deadline=None)
EXAMPLE = Path(__file__).parent / "data" / "village.rules.toml"
# 通用的規則偏稀疏；混進像村莊的規則，才常有靠近別人、被擠開的區域
spawn_rules = village_rules() | rules(entity_types=st.just("Spawn"), max_area=6)
# 大多排得下的規則：GEN-3 以外的 law 只看排得下的情況，篩掉太多會讓 hypothesis 放棄
fitting_rules = placeable_village_rules() | rules(entity_types=st.just("Spawn"), max_area=6)
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
            layers = scene.level(r.level_name)["layerInstances"] or []
            iids.append(sorted(e["iid"] for ly in layers for e in ly["entityInstances"]))
    assert written[0] == written[1]
    assert iids[0] == sorted(element_iid(r.seed, e.name) for e in r.entities)


@pytest.mark.law("GEN-2")
@FAST
@given(fitting_rules)
def test_gen_2_layout_fits_and_matches_scene(r: Rules) -> None:
    placed = _try_layout(r)
    assume(placed is not None)
    assert placed is not None
    width, height = r.level_size
    owner: dict[tuple[int, int], str] = {}
    for name, cells in placed.cells.items():
        assert cells, name
        for x, y in cells:
            assert 0 <= x < width and 0 <= y < height, (name, x, y)
            assert (x, y) not in owner, (name, owner.get((x, y)))
            owner[(x, y)] = name

    areas = {a.name: a for a in r.areas}
    for area in r.areas:
        x, y, w, h = _bbox(placed.cells[area.name])
        assert (w, h) == area.size
        assert len(placed.cells[area.name]) == w * h
        if area.near is None:
            continue
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
    assert len(set(placed.entities.values())) == len(placed.entities)

    with tempfile.TemporaryDirectory() as tmp:
        scene, _ = generate_scene(r, Path(tmp) / "scene.ldtk")
        save_scene(scene)
        level = load_scene(Path(tmp) / "scene.ldtk").level(r.level_name)
    value = {k.name: k.value for k in r.kinds}
    kind = {e.name: e.kind for e in (*r.areas, *r.paths)}
    layers = level["layerInstances"] or []
    intgrid = next(ly for ly in layers if ly["__type"] == "IntGrid")
    expected_csv = [
        value[kind[owner[(cx, cy)]]] if (cx, cy) in owner else 0
        for cy in range(height)
        for cx in range(width)
    ]
    assert intgrid["intGridCsv"] == expected_csv
    by_iid = {element_iid(r.seed, e.name): e.name for e in r.entities}
    got = {by_iid[e["iid"]]: tuple(e["__grid"]) for ly in layers for e in ly["entityInstances"]}
    assert got == placed.entities


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
    r = draw(village_rules())
    x = draw(st.sampled_from(r.areas))
    width, height = r.level_size
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
    """典型：Spawn 在主路通到南邊的盡頭，house_2 在廣場東邊隔 3 格。"""
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
[kinds]
k = { value = 1, color = "#000000" }
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
            (Kind("k", 1, "#000000"),),
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
        (Kind("k", 1, "#000000"),),
        (
            AreaRule("a", "k", (a[2], a[3]), at=(a[0], a[1])),
            AreaRule("b", "k", (b[2], b[3]), at=(b[0], b[1])),
        ),
        (PathRule("link", "k", "a", "b"),),
        (),
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
        (Kind("k", 1, "#000000"),),
        (AreaRule("x", "k", (1, 1), near="y"), AreaRule("y", "k", (1, 1), near="x")),
        (),
        (),
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
    second = EntityRule("spawn_2", "Spawn", "plaza.center")
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
        r = dataclasses.replace(base, entities=(EntityRule("ghost", "Spawn", "nowhere.center"),))
    elif change == "no_position":
        r = dataclasses.replace(base, areas=(*base.areas, AreaRule("lost", "k", (1, 1))))
    else:
        lost = AreaRule("lost", "k", (1, 1), near="nowhere")
        r = dataclasses.replace(base, areas=(*base.areas, lost))
    with pytest.raises(SceneError, match=where):
        layout(r)


def test_example_multi_level_generation_not_yet(tmp_path: Path) -> None:
    """多個 level 的生成還沒做（#36）：講清楚，不默默只生成第一個 level。"""
    r = load_rules(EXAMPLE.with_name("village_house.rules.toml"))
    with pytest.raises(SceneError, match="#36"):
        generate_scene(r, tmp_path / "scene.ldtk")
