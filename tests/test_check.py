"""CONV-1～CONV-3：慣例檢查 check_scene（D-011、#50）。"""

import asyncio
import copy
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from mcp.types import CallToolResult

from ldtk_map_creator.cli.main import main
from ldtk_map_creator.core import actions, edit
from ldtk_map_creator.core.check import check_scene
from ldtk_map_creator.core.derived import allocate_uids
from ldtk_map_creator.core.generate import generate_scene
from ldtk_map_creator.core.rules import Rules
from ldtk_map_creator.core.scene import SceneMap, add_level, load_scene, new_scene, save_scene
from ldtk_map_creator.mcp.server import server
from tests.test_generate import _try_layout, fitting_rules  # pyright: ignore[reportPrivateUsage]

SLOPE_PROBLEM = "斜坡四周沒有高一階的格"
LAYERS = ("Entities", "Collision", "Slope", "Height", "Ground")
INTGRID = LAYERS[1:]
LEVELS = ("Level_0", "House")
# Level_0 是 16x16；左上角 4x4 是小丘（Height 2），(1,4) 是斜坡；x ≥ 6 的格四周都是平地
FLAT_X = st.integers(6, 15)
Raw = dict[str, Any]


def _raw(scene: SceneMap) -> Raw:
    return cast(Raw, scene.project)


def _healthy() -> SceneMap:
    """照慣例的場景地圖：兩個 level、小丘加斜坡、出生點、互指的兩個傳送點、一個零件。"""
    scene = new_scene(Path("village.ldtk"))
    add_level(scene, "House", width_cells=6, height_cells=6, world_x=1024)
    edit.paint_rect(scene, "Level_0", x=0, y=0, width=4, height=4, value=2, layer="Height")
    edit.paint_cells(scene, "Level_0", cells=[(1, 4)], value=1, layer="Height")
    edit.paint_cells(scene, "Level_0", cells=[(1, 4)], value=1, layer="Slope")
    edit.paint_rect(scene, "Level_0", x=0, y=0, width=16, height=1, value=1, layer="Ground")
    edit.paint_rect(scene, "Level_0", x=8, y=8, width=2, height=2, value=1, layer="Collision")
    edit.place_entity(scene, "Level_0", "PlayerSpawn", x=6, y=6)
    door = edit.place_entity(scene, "Level_0", "Teleport", x=10, y=2)
    back = edit.place_entity(scene, "House", "Teleport", x=2, y=2)
    edit.set_entity_fields(scene, door["iid"], {"targetPort": back["iid"]})
    edit.set_entity_fields(scene, back["iid"], {"targetPort": door["iid"]})
    edit.place_entity(scene, "Level_0", "Part", x=12, y=12, values={"part": "oak", "yaw": 90})
    return scene


HEALTHY = _healthy()


def fresh() -> SceneMap:
    return copy.deepcopy(HEALTHY)


def _layer_def(scene: SceneMap, name: str) -> Raw:
    return next(d for d in _raw(scene)["defs"]["layers"] if d["identifier"] == name)


def _entity_def(scene: SceneMap, name: str) -> Raw:
    return next(d for d in _raw(scene)["defs"]["entities"] if d["identifier"] == name)


def _level(scene: SceneMap, name: str) -> Raw:
    return next(lv for lv in _raw(scene)["levels"] if lv["identifier"] == name)


def _layer(scene: SceneMap, level: str, name: str) -> Raw:
    return next(ly for ly in _level(scene, level)["layerInstances"] if ly["__identifier"] == name)


def _entities(scene: SceneMap, name: str) -> list[tuple[str, Raw]]:
    """（level 名稱, entity）；照 level、擺放的順序。"""
    return [
        (lv["identifier"], e)
        for lv in _raw(scene)["levels"]
        for ly in lv["layerInstances"]
        for e in ly["entityInstances"]
        if e["__identifier"] == name
    ]


def _set_field(scene: SceneMap, entity: Raw, field: str, editor_values: list[object]) -> None:
    fdef = next(
        f
        for f in _entity_def(scene, entity["__identifier"])["fieldDefs"]
        if f["identifier"] == field
    )
    instance = next(f for f in entity["fieldInstances"] if f["defUid"] == fdef["uid"])
    instance["realEditorValues"] = editor_values


def _ref(iid: str) -> list[object]:
    return [{"id": "V_String", "params": [iid]}]


def _short(entity: Raw) -> str:
    return str(entity["iid"])[:8]


# 每一種違反：改壞傳進來的場景地圖，回傳應該報出的那一行
Violation = Callable[[st.DrawFn, SceneMap], str]


def _missing_layer_def(draw: st.DrawFn, scene: SceneMap) -> str:
    name = draw(st.sampled_from(LAYERS))
    defs = _raw(scene)["defs"]["layers"]
    defs.remove(_layer_def(scene, name))
    return f"defs/{name}：缺少這一層的定義"


def _duplicate_layer_def(draw: st.DrawFn, scene: SceneMap) -> str:
    name = draw(st.sampled_from(LAYERS))
    twin = copy.deepcopy(_layer_def(scene, name))
    twin["uid"] = allocate_uids(scene.project, 1)[0]
    _raw(scene)["defs"]["layers"].append(twin)
    return f"defs/{name}：有 2 個同名的層定義，只能有一個"


def _wrong_layer_type(draw: st.DrawFn, scene: SceneMap) -> str:
    name = draw(st.sampled_from(LAYERS))
    ldef = _layer_def(scene, name)
    wanted = ldef["type"]
    ldef["type"] = "IntGrid" if wanted == "Entities" else "Entities"
    return f"defs/{name}：型別是 {ldef['type']}，要是 {wanted}"


def _slope_declares_more(draw: st.DrawFn, scene: SceneMap) -> str:
    value = draw(st.integers(2, 9) | st.integers(-9, 0))
    extra = copy.deepcopy(_layer_def(scene, "Slope")["intGridValues"][0])
    extra["value"], extra["identifier"] = value, "more"
    _layer_def(scene, "Slope")["intGridValues"].append(extra)
    return f"defs/Slope：宣告了值 {value}，只能宣告 1"


def _height_declares_low(draw: st.DrawFn, scene: SceneMap) -> str:
    value = draw(st.integers(-9, 0))
    extra = copy.deepcopy(_layer_def(scene, "Height")["intGridValues"][0])
    extra["value"], extra["identifier"] = value, "low"
    _layer_def(scene, "Height")["intGridValues"].append(extra)
    return f"defs/Height：宣告了值 {value}，要 ≥ 1"


def _missing_entity_def(draw: st.DrawFn, scene: SceneMap) -> str:
    name = draw(st.sampled_from(["PlayerSpawn", "Teleport", "Part"]))
    _raw(scene)["defs"]["entities"].remove(_entity_def(scene, name))
    return f"defs/{name}：缺少這個 entity 定義"


def _spawn_loses_tag(draw: st.DrawFn, scene: SceneMap) -> str:
    _entity_def(scene, "PlayerSpawn")["tags"] = draw(st.sampled_from([[], ["npc"]]))
    return "defs/PlayerSpawn：缺少 tags player"


FIELDS = (
    ("Teleport", "targetPort", "EntityRef"),
    ("Part", "part", "String"),
    ("Part", "yaw", "Float"),
)


def _missing_field_def(draw: st.DrawFn, scene: SceneMap) -> str:
    name, field, kind = draw(st.sampled_from(FIELDS))
    fields = _entity_def(scene, name)["fieldDefs"]
    fields[:] = [f for f in fields if f["identifier"] != field]
    return f"defs/{name}：缺少欄位 {field}（{kind}）"


def _wrong_field_type(draw: st.DrawFn, scene: SceneMap) -> str:
    name, field, kind = draw(st.sampled_from(FIELDS))
    fdef = next(f for f in _entity_def(scene, name)["fieldDefs"] if f["identifier"] == field)
    fdef["__type"] = draw(
        st.sampled_from([t for t in ("Int", "Bool", "String", "Float") if t != kind])
    )
    return f"defs/{name}：欄位 {field} 是 {fdef['__type']}，要是 {kind}"


def _missing_layer_instance(draw: st.DrawFn, scene: SceneMap) -> str:
    level, name = draw(st.sampled_from(LEVELS)), draw(st.sampled_from(LAYERS))
    layers = _level(scene, level)["layerInstances"]
    layers.remove(_layer(scene, level, name))
    return f"{level}/{name}：沒有這一層的實例"


def _undeclared_value(draw: st.DrawFn, scene: SceneMap) -> str:
    level, name = draw(st.sampled_from(LEVELS)), draw(st.sampled_from(INTGRID))
    layer = _layer(scene, level, name)
    width = layer["__cWid"]
    x, y = draw(st.integers(0, width - 1)), draw(st.integers(0, layer["__cHei"] - 1))
    value = draw(st.integers(9, 50) | st.integers(-9, -1))
    layer["intGridCsv"][y * width + x] = value
    return f"{level}/{name} ({x},{y})：值 {value} 沒有宣告"


def _slope_on_flat_ground(draw: st.DrawFn, scene: SceneMap) -> str:
    if draw(st.booleans()):
        level, x, y = "Level_0", draw(FLAT_X), draw(st.integers(0, 15))
    else:
        level, x, y = "House", draw(st.integers(0, 5)), draw(st.integers(0, 5))
    layer = _layer(scene, level, "Slope")
    layer["intGridCsv"][y * layer["__cWid"] + x] = 1
    return f"{level}/Slope ({x},{y})：{SLOPE_PROBLEM}"


def _slope_loses_its_higher_neighbour(draw: st.DrawFn, scene: SceneMap) -> str:
    height = _layer(scene, "Level_0", "Height")
    height["intGridCsv"][3 * height["__cWid"] + 1] = draw(st.sampled_from([0, 1, 3]))
    return f"Level_0/Slope (1,4)：{SLOPE_PROBLEM}"


def _teleport_target(draw: st.DrawFn, scene: SceneMap) -> str:
    level, door = draw(st.sampled_from(_entities(scene, "Teleport")))
    where = f"{level}/Teleport {_short(door)}"
    mode = draw(st.sampled_from(["unset", "null", "self", "spawn", "missing"]))
    if mode in ("unset", "null"):
        _set_field(scene, door, "targetPort", [] if mode == "unset" else [None])
        # 沒設過的欄位用預設值：範本的 targetPort 沒有預設，所以也是空的
        return f"{where}：targetPort 是空的"
    if mode == "self":
        _set_field(scene, door, "targetPort", _ref(door["iid"]))
        return f"{where}：targetPort 指到自己"
    if mode == "spawn":
        spawn = _entities(scene, "PlayerSpawn")[0][1]
        _set_field(scene, door, "targetPort", _ref(spawn["iid"]))
        return f"{where}：targetPort 指到的是 PlayerSpawn，要指到 Teleport"
    missing = draw(st.uuids()).hex.replace("-", "")
    assume(all(missing != e["iid"] for _, e in _entities(scene, "Teleport")))
    _set_field(scene, door, "targetPort", _ref(missing))
    return f"{where}：targetPort 指到不存在的 entity {missing[:8]}"


def _blank_part(draw: st.DrawFn, scene: SceneMap) -> str:
    level, part = _entities(scene, "Part")[0]
    blank = draw(st.sampled_from(["", " ", "\t\n", None]))
    _set_field(
        scene, part, "part", [None] if blank is None else [{"id": "V_String", "params": [blank]}]
    )
    return f"{level}/Part {_short(part)}：part 是空白的"


VIOLATIONS: tuple[Violation, ...] = (
    _missing_layer_def,
    _duplicate_layer_def,
    _wrong_layer_type,
    _slope_declares_more,
    _height_declares_low,
    _missing_entity_def,
    _spawn_loses_tag,
    _missing_field_def,
    _wrong_field_type,
    _missing_layer_instance,
    _undeclared_value,
    _slope_on_flat_ground,
    _slope_loses_its_higher_neighbour,
    _teleport_target,
    _blank_part,
)


@st.composite
def broken_scene(draw: st.DrawFn) -> tuple[SceneMap, str]:
    """從照慣例的場景地圖出發，隨機注入一種違反；回傳（改壞的場景地圖, 應該報出的那一行）。"""
    scene = fresh()
    expected = draw(st.sampled_from(VIOLATIONS))(draw, scene)
    return scene, expected


def _has_slope(r: Rules) -> bool:
    slopes = {k.name for k in r.kinds if k.slope}
    return any(a.kind in slopes for a in r.areas) or any(p.kind in slopes for p in r.paths)


@pytest.mark.law("CONV-1")
@settings(max_examples=40, deadline=None)
@given(fitting_rules, st.booleans(), st.tuples(st.integers(1, 8), st.integers(1, 8)))
def test_conv_1_generated_and_new_scenes_pass(r: Rules, split: bool, size: tuple[int, int]) -> None:
    assume(_try_layout(r) is not None)
    with tempfile.TemporaryDirectory() as tmp:
        blank = new_scene(Path(tmp) / "blank.ldtk", split=split)
        add_level(blank, "Extra", width_cells=size[0], height_cells=size[1], world_x=4096)
        assert check_scene(blank) == []
        scene, _ = generate_scene(r, Path(tmp) / "scene.ldtk", split=split)
        problems = check_scene(scene)
    if _has_slope(r):
        # 規則可以把斜坡種類畫在沒有高一階鄰格的地方；除此之外一律通過
        assert all(p.endswith(f"：{SLOPE_PROBLEM}") for p in problems)
    else:
        assert problems == []


@pytest.mark.law("CONV-2")
@settings(max_examples=300, deadline=None)
@given(broken_scene())
def test_conv_2_every_violation_is_reported_with_its_position(
    case: tuple[SceneMap, str],
) -> None:
    scene, expected = case
    before = copy.deepcopy(scene)
    problems = check_scene(scene)
    assert expected in problems
    assert check_scene(scene) == problems  # 查兩次輸出相同
    assert scene == before  # 檢查不改場景地圖


@pytest.mark.law("CONV-2")
def test_conv_2_action_writes_nothing(tmp_path: Path) -> None:
    """操作層：有問題與沒問題都不改任何檔，不產生預覽圖。

    跟上一條分開：上一條查純函式（記憶體裡的場景地圖），這條要真的寫檔、跑操作，
    一份例子就夠，放進性質測試會讓每個例子都去碰磁碟。
    """
    path = tmp_path / "village.ldtk"
    scene = fresh()
    scene.path = path
    save_scene(scene)
    assert actions.check_scene(path).summary == "照分層慣例，沒有問題"
    saved = load_scene(path)
    _layer(saved, "Level_0", "Slope")["intGridCsv"][5 * 16 + 8] = 1
    save_scene(saved)
    snapshot = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = actions.check_scene(path)
    assert not result.ok
    assert result.previews == ()
    assert f"Level_0/Slope (8,5)：{SLOPE_PROBLEM}" in result.summary
    assert "共 1 處" in result.summary
    assert snapshot == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert not (tmp_path / "previews").exists()


EXTRAS = ("layer", "values", "tags", "fields", "entity")


@pytest.mark.law("CONV-3")
@settings(max_examples=40, deadline=None)
@given(st.sets(st.sampled_from(EXTRAS)), st.booleans())
def test_conv_3_convention_is_a_floor(extras: set[str], split: bool) -> None:
    scene = fresh()
    scene.project["externalLevels"] = split
    raw = _raw(scene)
    if "layer" in extras:
        extra = copy.deepcopy(_layer_def(scene, "Collision"))
        extra["identifier"], extra["uid"] = "Extra", allocate_uids(scene.project, 1)[0]
        raw["defs"]["layers"].append(extra)
        for level in raw["levels"]:
            twin = copy.deepcopy(_layer(scene, level["identifier"], "Collision"))
            twin["__identifier"], twin["layerDefUid"], twin["iid"] = "Extra", extra["uid"], ""
            level["layerInstances"].append(twin)
    if "values" in extras:
        for name in ("Collision", "Ground"):
            value = copy.deepcopy(_layer_def(scene, name)["intGridValues"][0])
            value["value"], value["identifier"] = 7, "more"
            _layer_def(scene, name)["intGridValues"].append(value)
        _layer(scene, "House", "Ground")["intGridCsv"][0] = 7
    if "tags" in extras:
        for name in ("PlayerSpawn", "Teleport", "Part"):
            _entity_def(scene, name)["tags"].append("extra")
    if "fields" in extras:
        edit.add_entity_def(scene, "Probe", field_types={"note": "String"})
        probe = _entity_def(scene, "Probe")["fieldDefs"][0]
        _entity_def(scene, "Part")["fieldDefs"].append(copy.deepcopy(probe) | {"uid": 9999})
    if "entity" in extras:
        edit.add_entity_def(scene, "Chest", field_types={"loot": "String"})
        edit.place_entity(scene, "House", "Chest", x=1, y=1, values={"loot": "key"})
    assert check_scene(scene) == []
    with tempfile.TemporaryDirectory() as tmp:
        scene.path = Path(tmp) / "village.ldtk"
        save_scene(scene)
        assert check_scene(load_scene(scene.path)) == []


def test_example_slope_needs_a_higher_neighbour_in_four_directions() -> None:
    """邊界：對角的高一階不算；換成南邊的格才算；斜坡在 level 邊上也照四個方向看。"""
    scene = fresh()
    flat = _layer(scene, "House", "Height")
    slope = _layer(scene, "House", "Slope")
    slope["intGridCsv"][0] = 1  # (0,0)：在角落
    flat["intGridCsv"][1 * 6 + 1] = 1  # (1,1) 高一階，只在對角
    assert f"House/Slope (0,0)：{SLOPE_PROBLEM}" in check_scene(scene)
    flat["intGridCsv"][1 * 6 + 0] = 1  # (0,1) 在南邊
    assert check_scene(scene) == []


def test_example_problems_are_listed_in_level_layer_cell_order() -> None:
    """同一類錯很多時逐條列，順序照 level、層、格。"""
    scene = fresh()
    for level in LEVELS:
        for name in ("Ground", "Collision"):
            _layer(scene, level, name)["intGridCsv"][1] = 9
    lines = check_scene(scene)
    assert lines == [
        "Level_0/Collision (1,0)：值 9 沒有宣告",
        "Level_0/Ground (1,0)：值 9 沒有宣告",
        "House/Collision (1,0)：值 9 沒有宣告",
        "House/Ground (1,0)：值 9 沒有宣告",
    ]


def test_example_entries(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """典型：CLI 沒問題結束碼 0、有問題 1；MCP 工具回傳文字。"""
    scene = fresh()
    scene.path = tmp_path / "village.ldtk"
    save_scene(scene)
    assert main(["check_scene", str(scene.path)]) == 0
    assert "沒有問題" in capsys.readouterr().out
    saved = load_scene(scene.path)
    _layer(saved, "House", "Collision")["intGridCsv"][0] = 9
    save_scene(saved)
    assert main(["check_scene", str(scene.path)]) == 1
    assert "House/Collision (0,0)：值 9 沒有宣告" in capsys.readouterr().out

    async def go() -> list[str]:
        result = await server.call_tool("check_scene", {"path": str(scene.path)})
        assert isinstance(result, CallToolResult)
        return [c.text for c in result.content if c.type == "text"]

    assert "House/Collision (0,0)：值 9 沒有宣告" in asyncio.run(go())[0]


def test_example_layer_grid_size_is_per_layer() -> None:
    """曾經壞過（#50 審查）：格數照每一層自己的格子大小算；Collision 用 8px 的格不是違反。"""
    scene = fresh()
    _layer_def(scene, "Collision")["gridSize"] = 8
    for level in _raw(scene)["levels"]:
        cells = (level["pxWid"] // 8) * (level["pxHei"] // 8)
        _layer(scene, level["identifier"], "Collision")["intGridCsv"] = [0] * cells
    assert check_scene(scene) == []


def test_example_names_come_from_definitions() -> None:
    """曾經壞過（#50 審查）：名稱從定義讀，過期的 __identifier 不算；同名的 entity 定義要報。"""
    scene = fresh()
    _, back = _entities(scene, "Teleport")[1]
    back["__identifier"] = "Part"  # 過期的衍生欄位：它還是 Teleport，門口指到它沒錯
    assert check_scene(scene) == []
    twin = copy.deepcopy(_entity_def(scene, "Teleport"))
    twin["uid"] = max(d["uid"] for d in _raw(scene)["defs"]["entities"]) + 100
    _raw(scene)["defs"]["entities"].append(twin)
    assert "defs/Teleport：有 2 個同名的 entity 定義，只能有一個" in check_scene(scene)
