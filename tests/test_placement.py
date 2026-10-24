"""GEN-10～GEN-12：entity 與傳送點指定位置與大小、分層慣例 entity 改顏色與 tags（D-014）。"""

import dataclasses
import re
import tempfile
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ldtk_map_creator.core.check import check_scene
from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.generate import element_iid, generate_scene, portal_iids
from ldtk_map_creator.core.layout import Layout, layout
from ldtk_map_creator.core.rules import (
    AreaRule,
    ConventionOverride,
    EntityRule,
    FixedEnd,
    Kind,
    PortalRule,
    Rules,
    load_rules,
)
from ldtk_map_creator.core.scene import SceneMap, load_scene, save_scene
from tests.ldtk_schema import level_file_errors, load_json, project_errors
from tests.rule_strategies import placement_rules

FAST = settings(max_examples=100, deadline=None)
EXAMPLE = Path(__file__).parent / "data" / "placement.rules.toml"


def _generated(r: Rules) -> tuple[SceneMap, Layout]:
    """生成、寫檔、再讀回來：測的是磁碟上的樣子，寫出的每個檔都要通過官方 schema（FILE-1）。"""
    with tempfile.TemporaryDirectory() as tmp:
        scene, placed = generate_scene(r, Path(tmp) / "scene.ldtk")
        save_scene(scene)
        for path in Path(tmp).rglob("*.ldtk*"):
            data = load_json(path)
            errors = project_errors(data) if path.suffix == ".ldtk" else level_file_errors(data)
            assert errors == [], path.name
        return load_scene(Path(tmp) / "scene.ldtk"), placed


def _instances(scene: SceneMap) -> dict[str, tuple[str, Any]]:
    """每個 entity 實例：iid → （所在的 level, 實例）。"""
    return {
        e["iid"]: (lv["identifier"], e)
        for lv in scene.project["levels"]
        for ly in lv["layerInstances"] or []
        for e in ly["entityInstances"]
    }


def _anchor_cell(r: Rules, anchor: str) -> tuple[str, tuple[int, int]]:
    """區域的錨點在哪個 level、哪一格；placement_rules 的區域都是固定位置，獨立於配置器另算。"""
    owner, _, which = anchor.partition(".")
    area = next(a for a in r.areas if a.name == owner)
    assert isinstance(area.at, tuple)
    (x, y), (w, h) = area.at, area.size
    dx = {"west": 0, "east": w - 1}.get(which, w // 2)
    dy = {"north": 0, "south": h - 1}.get(which, h // 2)
    return r.level_of(area).name, (x + dx, y + dy)


@pytest.mark.law("GEN-10")
@FAST
@given(placement_rules())
def test_gen_10_entity_size_offset_and_resizable(r: Rules) -> None:
    loaded, _ = _generated(r)
    grid = r.grid
    found = _instances(loaded)
    declared = {t.name: t.size for t in r.entity_types}
    sizes = {lv.name: lv.size for lv in r.levels}
    for rule in r.entities:
        level, e = found[element_iid(r.seed, rule.name)]
        if isinstance(rule.at, str):
            owner, cell = _anchor_cell(r, rule.at)
            assert owner == level
        else:
            cell = rule.at
        assert level == r.level_of(rule).name
        w, h = (
            (rule.size[0] * grid, rule.size[1] * grid)
            if rule.size
            else declared.get(rule.type, (grid, grid))
        )
        assert (e["width"], e["height"]) == (w, h)
        assert e["px"] == [cell[0] * grid + rule.offset[0], cell[1] * grid + rule.offset[1]]
        assert e["__grid"] == list(cell)  # offset 小於一格，實例還在那一格
        width, height = sizes[level]
        assert e["px"][0] + w <= width * grid and e["px"][1] + h <= height * grid
    # 有實例的大小跟定義不同的定義才可以縮放；其餘維持範本的值（不可縮放）
    for edef in loaded.project["defs"]["entities"]:
        differs = any(
            (e["width"], e["height"]) != (edef["width"], edef["height"])
            for _, e in found.values()
            if e["defUid"] == edef["uid"]
        )
        assert (edef["resizableX"], edef["resizableY"]) == (differs, differs), edef["identifier"]
    assert check_scene(loaded) == []


@pytest.mark.law("GEN-11")
@FAST
@given(placement_rules())
def test_gen_11_portal_ends_keep_position_and_size(r: Rules) -> None:
    loaded, placed = _generated(r)
    grid = r.grid
    found = _instances(loaded)
    claimed: set[tuple[str, tuple[int, int]]] = set()
    claimed |= {(placed.level_of[e.name], placed.entities[e.name]) for e in r.entities}
    for portal in r.portals:
        for iid, end, sized in zip(
            portal_iids(r.seed, portal.name),
            (portal.start, portal.to),
            placed.portal_sizes[portal.name],
            strict=True,
        ):
            level, e = found[iid]
            if isinstance(end, FixedEnd):
                want_level, cell, size = end.level or r.levels[0].name, end.at, end.size
            else:
                (want_level, cell), size = _anchor_cell(r, end), (1, 1)
            assert (level, sized) == (want_level, size)
            assert e["__identifier"] == "Teleport"
            assert e["px"] == [cell[0] * grid, cell[1] * grid]
            assert (e["width"], e["height"]) == (size[0] * grid, size[1] * grid)
            # 端點的每一格都不跟同一個 level 的別的 entity 或端點同格
            cells = {
                (level, (cell[0] + dx, cell[1] + dy))
                for dx in range(size[0])
                for dy in range(size[1])
            }
            assert not cells & claimed, (portal.name, cells & claimed)
            claimed |= cells


@pytest.mark.law("GEN-12")
@FAST
@given(placement_rules())
def test_gen_12_convention_overrides_only_change_color_and_tags(r: Rules) -> None:
    plain, _ = _generated(dataclasses.replace(r, overrides=()))
    loaded, _ = _generated(r)
    got = {d["identifier"]: d for d in loaded.project["defs"]["entities"]}
    before = {d["identifier"]: d for d in plain.project["defs"]["entities"]}
    over = {o.name: o for o in r.overrides}
    for name in ("PlayerSpawn", "Teleport", "Part"):
        wanted = dict(before[name])  # 名稱、大小、欄位定義等，其餘一律跟沒覆寫時一樣
        if name in over and over[name].color is not None:
            wanted["color"] = over[name].color
        if name in over and over[name].tags is not None:
            wanted["tags"] = list(over[name].tags or ())
        assert got[name] == wanted, name
    for _, e in _instances(loaded).values():  # 實例的 __tags 與顏色跟著定義
        assert e["__tags"] == got[e["__identifier"]]["tags"]
        assert e["__smartColor"] == got[e["__identifier"]]["color"]
    assert check_scene(loaded) == []


def test_example_f1_each_house_has_its_own_size(tmp_path: Path) -> None:
    """F1：同一個 House 類型，沒寫 size 是類型的 4×3 格，寫了就是 3×3、6×3 格。"""
    scene, _ = generate_scene(load_rules(EXAMPLE), tmp_path / "scene.ldtk")
    found = _instances(scene)
    r = load_rules(EXAMPLE)
    sizes = {e.name: found[element_iid(r.seed, e.name)][1] for e in r.entities if e.type == "House"}
    assert {n: (i["width"], i["height"]) for n, i in sizes.items()} == {
        "house_a": (384, 288),
        "house_b": (288, 288),
        "house_c": (576, 288),
    }
    house = next(d for d in scene.project["defs"]["entities"] if d["identifier"] == "House")
    assert (house["width"], house["height"]) == (384, 288)  # 定義還是類型的大小
    assert house["resizableX"] and house["resizableY"]


def test_example_f2_spawn_sits_inside_its_cell(tmp_path: Path) -> None:
    """F2：出生點在 (4, 3) 那一格往右 48、往下 72，px 是 (432, 360)，仍算在那一格；8×8 的也一樣。"""
    r = load_rules(EXAMPLE)
    scene, _ = generate_scene(r, tmp_path / "scene.ldtk")
    found = _instances(scene)
    _, spawn = found[element_iid(r.seed, "spawn")]
    assert spawn["px"] == [432, 360] and spawn["__grid"] == [4, 3]
    assert (spawn["width"], spawn["height"]) == (96, 96)
    # 比一格小的實例：宣告像素大小的類型（8×8），再用 offset 擺進 (6, 5) 那一格
    _, scout = found[element_iid(r.seed, "scout")]
    assert scout["px"] == [6 * 96 + 48, 5 * 96 + 72] and scout["__grid"] == [6, 5]
    assert (scout["width"], scout["height"]) == (8, 8)
    assert scout["__tags"] == ["player"]


def test_example_f3_portal_ends_are_bands(tmp_path: Path) -> None:
    """F3：1×3 與 3×1 的傳送點，兩端各一個 Teleport，位置與大小照寫，targetPort 互指。"""
    r = load_rules(EXAMPLE)
    scene, placed = generate_scene(r, tmp_path / "scene.ldtk")
    found = _instances(scene)
    door_a, door_b = portal_iids(r.seed, "home_door")
    south_a, south_b = portal_iids(r.seed, "farm_south")
    got = {
        iid: (found[iid][0], found[iid][1]["px"], found[iid][1]["width"], found[iid][1]["height"])
        for iid in (door_a, door_b, south_a, south_b)
    }
    assert got == {
        door_a: ("Home", [0, 288], 96, 288),
        door_b: ("Farm", [0, 960], 96, 288),
        south_a: ("Farm", [1728, 1824], 288, 96),
        south_b: ("Home", [288, 672], 288, 96),
    }
    assert placed.portal_sizes == {"home_door": ((1, 3), (1, 3)), "farm_south": ((3, 1), (3, 1))}
    teleport = next(d for d in scene.project["defs"]["entities"] if d["identifier"] == "Teleport")
    assert (teleport["width"], teleport["height"]) == (96, 96)  # 定義的大小不變
    assert teleport["resizableX"] and teleport["resizableY"]
    refs = {
        iid: next(f for f in found[iid][1]["fieldInstances"] if f["__identifier"] == "targetPort")
        for iid in (door_a, door_b)
    }
    assert refs[door_a]["__value"]["entityIid"] == door_b
    assert refs[door_b]["__value"]["entityIid"] == door_a


def test_example_f4_teleport_color_and_tags(tmp_path: Path) -> None:
    """F4：Teleport 的顏色與 tags 照規則檔，實例的 __tags 跟著；欄位與大小不變，通過慣例檢查。"""
    r = load_rules(EXAMPLE)
    scene, _ = generate_scene(r, tmp_path / "scene.ldtk")
    teleport = next(d for d in scene.project["defs"]["entities"] if d["identifier"] == "Teleport")
    assert (teleport["color"], teleport["tags"]) == ("#30e1b9", ["tele"])
    assert [f["identifier"] for f in teleport["fieldDefs"]] == ["targetPort"]
    for iid in portal_iids(r.seed, "home_door"):
        assert _instances(scene)[iid][1]["__tags"] == ["tele"]
    assert check_scene(scene) == []
    spawn = next(d for d in scene.project["defs"]["entities"] if d["identifier"] == "PlayerSpawn")
    assert spawn["tags"] == ["player"]  # 沒覆寫的維持範本的值


def test_example_overrides_do_not_touch_other_definitions(tmp_path: Path) -> None:
    """邊界：覆寫一個分層慣例 entity，另外兩個、宣告的類型都不受影響。"""
    r = load_rules(EXAMPLE)
    plain = dataclasses.replace(r, overrides=())
    a, _ = generate_scene(r, tmp_path / "a.ldtk")
    b, _ = generate_scene(plain, tmp_path / "b.ldtk")
    untouched = {"PlayerSpawn", "Part", "House"}

    def defs(scene: SceneMap) -> list[Any]:
        return [d for d in scene.project["defs"]["entities"] if d["identifier"] in untouched]

    assert defs(a) == defs(b)


@pytest.mark.parametrize(
    ("change", "where"),
    [
        # 位移把整個矩形推出 level：Home 是 10×8 格，(9, 7) 那一格加上 offset 就出界
        ({"at": (9, 7)}, r"^spawn\.at：\(9, 7\) 加上 offset \(48, 72\)"),
        # 大小比 level 還大
        ({"size": (11, 1)}, r"^spawn\.size：11x1 比 level 的 10x8 大"),
        # 擺在最後一格、再寫 size 就出界
        ({"at": (9, 0), "offset": (0, 0), "size": (2, 1)}, r"^spawn\.at：\(9, 0\)"),
    ],
)
def test_example_entity_rect_outside_level_names_the_entity(
    change: dict[str, Any], where: str
) -> None:
    """邊界（GEN-3）：實體矩形（含 offset、size）超出 level，配置器擋下，訊息開頭是元素與欄位。"""
    r = load_rules(EXAMPLE)
    spawn = dataclasses.replace(r.entities[0], **change)
    with pytest.raises(SceneError, match=where):
        layout(dataclasses.replace(r, entities=(spawn, *r.entities[1:])))


def test_example_anchored_entity_rect_is_checked_after_layout() -> None:
    """邊界（GEN-3）：擺在錨點的 entity，錨點算出來之後才知道矩形出不出界（讀規則檔時查不到）。"""
    r = load_rules(EXAMPLE)
    edge = EntityRule("edge", "PlayerSpawn", "home_floor.east", level="Home", size=(2, 1))
    r = dataclasses.replace(r, entities=(edge,), portals=())
    with pytest.raises(SceneError, match=r"^edge\.at：\(9, 4\) 加上 offset \(0, 0\)"):
        layout(r)


@pytest.mark.parametrize(
    ("end", "where"),
    [
        (
            FixedEnd("Home", (9, 0), (2, 1)),
            r"^door\.from\.at：\(9, 0\) 放 2x1 會超出 level 的 10x8",
        ),
        (FixedEnd("Home", (0, 0), (1, 9)), r"^door\.from\.size：1x9 比 level 的 10x8 大"),
        (FixedEnd("Nowhere", (0, 0)), r"^door\.from\.level：沒有叫 Nowhere 的 level"),
        (FixedEnd(None, (0, 0)), r"^door\.from\.level：有好幾個 level 時要寫在哪個 level"),
    ],
)
def test_example_fixed_end_mistakes_name_the_portal(end: FixedEnd, where: str) -> None:
    """邊界（GEN-3）：固定端點超出 level、level 不存在、沒寫 level，配置器也擋，訊息開頭是端點。"""
    r = load_rules(EXAMPLE)
    door = PortalRule("door", end, FixedEnd("Farm", (0, 10)))
    with pytest.raises(SceneError, match=where):
        layout(dataclasses.replace(r, portals=(door,)))


def test_example_fixed_end_collides_with_what_is_already_there() -> None:
    """邊界（GEN-3）：端點的矩形蓋到別的 entity、零件或端點的格，報在後擺的那個端點。"""
    r = load_rules(EXAMPLE)
    door = r.portals[0]
    # entity 的格在 home_door.from（Home 的 (0, 3) 起 1×3）裡：entity 先擺，端點後擺
    bump = EntityRule("bump", "PlayerSpawn", (0, 5), level="Home")
    with pytest.raises(SceneError, match=r"^home_door\.from：跟「bump」擺在同一格 \(0, 5\)"):
        layout(dataclasses.replace(r, entities=(*r.entities, bump)))
    # 另一個傳送點的端點跟它重疊 1 格
    other = PortalRule("other", FixedEnd("Home", (0, 5), (3, 1)), FixedEnd("Farm", (5, 5)))
    with pytest.raises(SceneError, match=r"^other\.from：跟「home_door\.from」擺在同一格 \(0, 5\)"):
        layout(dataclasses.replace(r, portals=(door, other)))
    # 同一個傳送點的兩端重疊
    twin = PortalRule("twin", FixedEnd("Home", (1, 1), (2, 2)), FixedEnd("Home", (2, 2), (2, 2)))
    with pytest.raises(SceneError, match=r"^twin\.to：跟「twin\.from」擺在同一格 \(2, 2\)"):
        layout(dataclasses.replace(r, portals=(twin,)))


def test_example_fixed_end_may_sit_on_areas() -> None:
    """典型：端點可以蓋在區域的格上（門在地板上），只跟別的 entity、零件、端點互斥。"""
    r = load_rules(EXAMPLE)
    placed = layout(r)
    floor = placed.cells["home_floor"]
    cell = placed.portals["home_door"][0][1]
    assert cell in floor  # home_door 的 Home 那端落在地板區域上


def test_example_fixed_end_collides_with_an_area_part() -> None:
    """邊界（GEN-3）：端點的矩形蓋到帶零件區域的中心（零件的格），報在端點。"""
    r = load_rules(EXAMPLE)
    kinds = (*r.kinds, Kind("shrine", ground="dirt", part="shrine"))
    shrine = AreaRule("shrine_a", "shrine", (3, 3), at=(30, 3), level="Farm")  # 中心 (31, 4)
    door = PortalRule("door", FixedEnd("Farm", (30, 4), (3, 1)), FixedEnd("Home", (0, 0)))
    r = dataclasses.replace(r, kinds=kinds, areas=(*r.areas, shrine), portals=(door,))
    with pytest.raises(SceneError, match=r"^door\.from：跟「shrine_a 的零件」擺在同一格 \(31, 4\)"):
        layout(r)


@pytest.mark.parametrize(
    ("what", "where"),
    [
        ("offset_negative", r"^spawn\.offset：要小於格子大小 96"),
        ("offset_too_big", r"^spawn\.offset：要小於格子大小 96"),
        ("size_zero", r"^spawn\.size：至少是 1"),
        ("end_size_zero", r"^door\.from\.size：至少是 1"),
        ("end_negative", r"^door\.from\.at：不能是負的"),
        ("override_without_player", r"^entity_types\.PlayerSpawn\.tags："),
        ("override_bad_color", r"^entity_types\.Teleport\.color："),
        ("override_empty", r"^entity_types\.Part："),
    ],
)
def test_example_unparsed_placement_mistakes_are_scene_errors(what: str, where: str) -> None:
    """邊界：沒經過規則檔檢查、直接組出來的規則寫錯，生成也丟 SceneError，不悄悄生成錯的地圖。"""
    r = load_rules(EXAMPLE)
    spawn = r.entities[0]
    if what == "offset_negative":
        r = dataclasses.replace(r, entities=(dataclasses.replace(spawn, offset=(-1, 0)),))
    elif what == "offset_too_big":
        r = dataclasses.replace(r, entities=(dataclasses.replace(spawn, offset=(0, 200)),))
    elif what == "size_zero":
        r = dataclasses.replace(r, entities=(dataclasses.replace(spawn, size=(0, 1)),))
    elif what == "end_size_zero":
        door = PortalRule("door", FixedEnd("Home", (0, 0), (0, 1)), FixedEnd("Farm", (0, 10)))
        r = dataclasses.replace(r, portals=(door,))
    elif what == "end_negative":
        door = PortalRule("door", FixedEnd("Home", (-1, 0)), FixedEnd("Farm", (0, 10)))
        r = dataclasses.replace(r, portals=(door,))
    elif what == "override_without_player":
        r = dataclasses.replace(r, overrides=(ConventionOverride("PlayerSpawn", None, ()),))
    elif what == "override_bad_color":
        r = dataclasses.replace(r, overrides=(ConventionOverride("Teleport", "red"),))
    else:
        r = dataclasses.replace(r, overrides=(ConventionOverride("Part"),))
    with tempfile.TemporaryDirectory() as tmp, pytest.raises(SceneError, match=where):
        generate_scene(r, Path(tmp) / "scene.ldtk")
        pytest.fail("generate_scene 沒有丟錯")


@st.composite
def broken_layout(draw: st.DrawFn) -> tuple[Rules, str]:
    """排得下的規則弄壞一處（位置與大小的衝突）；回傳弄壞的規則，與訊息開頭該有的位置。"""
    good = draw(placement_rules().filter(lambda r: bool(r.entities)))
    placed = layout(good)
    many = len(good.levels) > 1

    def end_at(level: str, cell: tuple[int, int]) -> FixedEnd:
        return FixedEnd(level if many else None, cell)

    options = ["rect_out", "end_out", "end_on_entity"]
    if good.portals:
        options.append("end_on_end")
    match draw(st.sampled_from(options)):
        case "rect_out":
            # 實體往右拉到超出 level：大小比 level 還大報在 size，否則報在 at
            entity = draw(st.sampled_from(good.entities))
            width = next(
                lv.size[0] for lv in good.levels if lv.name == placed.level_of[entity.name]
            )
            size = (width - placed.entities[entity.name][0] + 1, 1)
            bad = dataclasses.replace(entity, size=size, offset=(0, 0))
            entities = tuple(bad if e is entity else e for e in good.entities)
            field = "size" if size[0] > width else "at"
            return dataclasses.replace(good, entities=entities), f"{entity.name}.{field}"
        case "end_out":
            level = draw(st.sampled_from(good.levels))
            portal = PortalRule("zz_out", end_at(level.name, (level.size[0], 0)), "nowhere")
            return dataclasses.replace(good, portals=(*good.portals, portal)), "zz_out.from.at"
        case "end_on_entity":
            # 新的傳送點的端點蓋住一個 entity 的左上角那一格：entity 先擺，報在後擺的端點
            entity = draw(st.sampled_from(good.entities))
            end = end_at(placed.level_of[entity.name], placed.entities[entity.name])
            portal = PortalRule("zz_hit", end, end)
            return dataclasses.replace(good, portals=(*good.portals, portal)), "zz_hit.from"
        case _:
            # 新的傳送點的端點蓋住別的端點的左上角那一格：先擺的先佔，報在後擺的端點
            existing = draw(st.sampled_from(good.portals))
            level, cell = placed.portals[existing.name][0]
            end = end_at(level, cell)
            portal = PortalRule("zz_over", end, end)
            return dataclasses.replace(good, portals=(*good.portals, portal)), "zz_over.from"


@pytest.mark.law("GEN-3")
@FAST
@given(broken_layout())
def test_gen_3_placement_failures_name_the_element(case: tuple[Rules, str]) -> None:
    """D-014 的衝突：矩形出界、端點出界、端點撞到 entity 或別的端點，訊息開頭是出錯的元素與欄位。"""
    r, where = case
    with pytest.raises(SceneError, match=rf"^{re.escape(where)}："):
        layout(r)
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(SceneError, match=rf"^{re.escape(where)}："):
            generate_scene(r, Path(tmp) / "scene.ldtk")
        assert list(Path(tmp).iterdir()) == []  # 丟錯時還沒寫任何檔
