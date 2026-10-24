"""RENDER-1～RENDER-5：預覽圖。"""

import copy
import dataclasses
import io
import tempfile
from pathlib import Path
from typing import cast

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from PIL import Image, ImageChops

from ldtk_map_creator.core import record
from ldtk_map_creator.core.edit import add_entity_def, paint_cells, place_entity
from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.generate import generate_scene
from ldtk_map_creator.core.model import LayerInstance
from ldtk_map_creator.core.record import name_positions
from ldtk_map_creator.core.render import (
    MARGIN,
    image_size,
    name_boxes,
    render_level,
    render_world,
    world_links,
    world_view,
    write_preview,
)
from ldtk_map_creator.core.rules import AreaRule, EntityRule, Kind, Rules
from ldtk_map_creator.core.scene import SceneMap, add_level, load_scene, new_scene, save_scene
from tests.rule_strategies import (
    SIMPLE_GROUNDS,
    SIMPLE_KINDS,
    one_level_rules,
    placeable_multi_village_rules,
    placeable_village_rules,
)

FAST = settings(max_examples=30, deadline=None)

cells = st.tuples(st.integers(0, 9), st.integers(0, 9))


def _hex(color: str) -> tuple[int, int, int, int]:
    v = color.lstrip("#")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), 255)


def _build(
    folder: Path, w: int, h: int, painted: list[tuple[int, int]], spawns: list[tuple[int, int]]
) -> SceneMap:
    scene = new_scene(folder / "scene.ldtk")
    add_level(scene, "Room", width_cells=w, height_cells=h, world_x=4096)
    inside = [(x, y) for x, y in painted if x < w and y < h]
    paint_cells(scene, "Room", cells=inside, value=1, layer="Collision")
    for x, y in spawns:
        if x < w and y < h:
            place_entity(scene, "Room", "PlayerSpawn", x=x, y=y)
    return scene


def _layer(scene: SceneMap, level: str, name: str) -> LayerInstance:
    return next(
        ly for ly in scene.level(level)["layerInstances"] or [] if ly["__identifier"] == name
    )


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


scenes = st.tuples(
    st.integers(1, 10),
    st.integers(1, 10),
    st.lists(cells, max_size=30),
    st.lists(cells, max_size=4),
    st.integers(1, 3),
)


@pytest.mark.law("RENDER-1")
@pytest.mark.law("RENDER-2")
@pytest.mark.law("RENDER-3")
@FAST
@given(scenes)
def test_render_1_to_3_size_and_colors(
    case: tuple[int, int, list[tuple[int, int]], list[tuple[int, int]], int],
) -> None:
    w, h, painted, spawns, scale = case
    with tempfile.TemporaryDirectory() as tmp:
        scene = _build(Path(tmp), w, h, painted, spawns)
        level = scene.level("Room")
        image = render_level(scene, "Room", scale=scale)
        assert image.size == image_size(level, scale)
        assert image.size == (MARGIN + level["pxWid"] * scale, MARGIN + level["pxHei"] * scale)
        defs = scene.project["defs"]
        collision = next(d for d in defs["layers"] if d["identifier"] == "Collision")
        wall = _hex(collision["intGridValues"][0]["color"])
        spawn = _hex(defs["entities"][0]["color"])
        background = _hex(level["__bgColor"])
        cell = 16 * scale
        placed = {(x, y) for x, y in spawns if x < w and y < h}
        # entity 名稱寫在方塊上方、貼著上緣時寫在下方、都放不下時寫在方塊裡，
        # 而且可能比一格寬：上、下、同一列整列都不驗（entity 自己的格照驗）
        label_rows = {row for _, y in placed for row in (y - 1, y, y + 1)}
        covered = placed | {(x, y) for y in label_rows for x in range(w)}
        filled = {(x, y) for x, y in painted if x < w and y < h}
        for cy in range(h):
            for cx in range(w):
                center = (MARGIN + cx * cell + cell // 2, MARGIN + cy * cell + cell // 2)
                pixel = image.getpixel(center)
                if (cx, cy) in placed:
                    assert pixel == spawn
                elif (cx, cy) not in covered:
                    assert pixel == (wall if (cx, cy) in filled else background)


@pytest.mark.law("RENDER-4")
@FAST
@given(scenes)
def test_render_4_is_deterministic(
    case: tuple[int, int, list[tuple[int, int]], list[tuple[int, int]], int],
) -> None:
    w, h, painted, spawns, scale = case
    with tempfile.TemporaryDirectory() as tmp:
        scene = _build(Path(tmp), w, h, painted, spawns)
        before = copy.deepcopy(scene.project)
        first = _png(render_level(scene, "Room", scale=scale))
        assert scene.project == before
        assert _png(render_level(scene, "Room", scale=scale)) == first
        save_scene(scene)
        assert _png(render_level(load_scene(scene.path), "Room", scale=scale)) == first


def test_example_write_preview_location(tmp_path: Path) -> None:
    """典型：預覽圖存在場景地圖旁的 previews/<主檔名>/<level>.png。"""
    scene = new_scene(tmp_path / "village.ldtk")
    path = write_preview(scene, "Level_0", render_level(scene, "Level_0"))
    assert path == tmp_path / "previews" / "village" / "Level_0.png"
    assert Image.open(path).size == image_size(scene.level("Level_0"), 2)


def test_example_scale_must_be_positive(tmp_path: Path) -> None:
    """邊界：倍率至少 1。"""
    scene = new_scene(tmp_path / "village.ldtk")
    with pytest.raises(SceneError):
        render_level(scene, "Level_0", scale=0)


def test_example_entity_uses_its_own_size_and_layer_offset(tmp_path: Path) -> None:
    """曾經壞過：entity 用自己的尺寸畫；圖層的像素偏移要算進去。"""
    scene = new_scene(tmp_path / "village.ldtk")
    entity = place_entity(scene, "Level_0", "PlayerSpawn", x=2, y=2)
    entity["width"] = 48
    layer = _layer(scene, "Level_0", "Entities")
    layer["pxOffsetX"] = 8
    image = render_level(scene, "Level_0", scale=1)
    spawn = _hex(scene.project["defs"]["entities"][0]["color"])
    # 方塊從 x = 2*16 + 8 起 48px；取右段、避開格線的一點
    assert image.getpixel((MARGIN + 32 + 8 + 44, MARGIN + 32 + 8)) == spawn
    assert image.getpixel((MARGIN + 32 + 4, MARGIN + 32 + 8)) != spawn


def test_example_hidden_layer_is_not_drawn(tmp_path: Path) -> None:
    """邊界：LDtk 裡隱藏的圖層不畫。"""
    scene = new_scene(tmp_path / "village.ldtk")
    paint_cells(scene, "Level_0", cells=[(3, 3)], value=1, layer="Collision")
    _layer(scene, "Level_0", "Collision")["visible"] = False
    image = render_level(scene, "Level_0", scale=1)
    background = _hex(scene.level("Level_0")["__bgColor"])
    assert image.getpixel((MARGIN + 3 * 16 + 8, MARGIN + 3 * 16 + 8)) == background


def test_example_undefined_value_is_scene_error(tmp_path: Path) -> None:
    """邊界：csv 裡有定義沒有的值，丟 SceneError 而不是 KeyError。"""
    scene = new_scene(tmp_path / "village.ldtk")
    _layer(scene, "Level_0", "Collision")["intGridCsv"][0] = 7
    with pytest.raises(SceneError):
        render_level(scene, "Level_0")


def test_example_stacked_entities_keep_their_color(tmp_path: Path) -> None:
    """曾經壞過：上下相鄰的 entity，下面那個的名稱不能蓋住上面那個方塊。"""
    scene = new_scene(tmp_path / "village.ldtk")
    place_entity(scene, "Level_0", "PlayerSpawn", x=2, y=1)
    place_entity(scene, "Level_0", "PlayerSpawn", x=2, y=2)
    spawn = _hex(scene.project["defs"]["entities"][0]["color"])
    image = render_level(scene, "Level_0", scale=1)
    for cy in (1, 2):
        assert image.getpixel((MARGIN + 2 * 16 + 8, MARGIN + cy * 16 + 8)) == spawn


def test_example_locked_preview_reports_scene_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """邊界：預覽圖被別的程式鎖住時重試，仍失敗就丟 SceneError，不留暫存檔。"""
    scene = new_scene(tmp_path / "village.ldtk")

    def locked(self: Path, target: Path) -> Path:
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "replace", locked)
    with pytest.raises(SceneError):
        write_preview(scene, "Level_0", render_level(scene, "Level_0"))
    assert list((tmp_path / "previews" / "village").glob("*.tmp")) == []


def test_example_grid_follows_visible_intgrid_offset(tmp_path: Path) -> None:
    """曾經壞過：格線跟著看得見的 IntGrid 的偏移，格子才對得上格線。"""
    scene = new_scene(tmp_path / "village.ldtk")
    _layer(scene, "Level_0", "Collision")["pxOffsetX"] = 8
    image = render_level(scene, "Level_0", scale=1)
    background = _hex(scene.level("Level_0")["__bgColor"])
    row = MARGIN + 8
    assert image.getpixel((MARGIN + 16 + 8, row)) != background  # 偏移後的格線
    assert image.getpixel((MARGIN + 16, row)) == background  # 沒偏移的位置不畫線


def test_example_bad_color_and_zero_size_are_scene_errors(tmp_path: Path) -> None:
    """邊界：顏色不是十六進位、entity 尺寸是 0，都丟 SceneError。"""
    scene = new_scene(tmp_path / "village.ldtk")
    scene.project["defs"]["entities"][0]["color"] = "#GG0000"
    place_entity(scene, "Level_0", "PlayerSpawn", x=0, y=0)
    with pytest.raises(SceneError):
        render_level(scene, "Level_0")
    scene.project["defs"]["entities"][0]["color"] = "#BE4A2F"
    entity = _layer(scene, "Level_0", "Entities")["entityInstances"][0]
    entity["width"] = 0
    with pytest.raises(SceneError):
        render_level(scene, "Level_0")


def test_example_label_in_one_row_level_stays_inside(tmp_path: Path) -> None:
    """邊界：只有一列高的 level，名稱寫進方塊裡，不跑出圖外。"""
    scene = new_scene(tmp_path / "village.ldtk")
    add_level(scene, "Strip", width_cells=6, height_cells=1, world_x=4096)
    place_entity(scene, "Strip", "PlayerSpawn", x=0, y=0)
    image = render_level(scene, "Strip", scale=2)
    box = image.crop((MARGIN, MARGIN, MARGIN + 32, MARGIN + 32))
    # 不含外框；字有反鋸齒，找比方塊顏色亮很多的像素（方塊的綠色分量是 0x4A）
    inside = [box.getpixel((x, y)) for x in range(2, 30) for y in range(2, 30)]
    assert any(isinstance(p, tuple) and p[1] > 150 for p in inside)


@pytest.mark.law("RENDER-5")
@settings(max_examples=40, deadline=None)
@given(placeable_village_rules(), st.integers(1, 3), st.integers(0, 99))
def test_render_5_element_names(r: Rules, scale: int, pick: int) -> None:
    # 名稱畫在 entity 下面，可能被蓋住量不到；跟 entity 的關係由例子另外驗
    # 區域零件（D-013）也是 entity，擺在區域中心；一樣去掉
    kinds = tuple(dataclasses.replace(k, part=None, yaw=0.0) for k in r.kinds)
    r = dataclasses.replace(r, entities=(), kinds=kinds)
    with tempfile.TemporaryDirectory() as tmp:
        scene, placed = generate_scene(r, Path(tmp) / "scene.ldtk")
    names = name_positions(placed, {a.name for a in r.areas})
    assume(names)
    for name, x, y in names:  # 每個名稱都寫在自己的格上
        assert (int(x), int(y)) in placed.cells[name], name
    level = scene.level(r.level_name)
    inner_right = MARGIN + level["pxWid"] * scale
    inner_bottom = MARGIN + level["pxHei"] * scale
    # 全部一起寫：都在 level 裡、彼此不重疊
    boxes = [b for _, _, _, b in name_boxes(scene, r.level_name, names, scale=scale)]
    for i, a in enumerate(boxes):
        assert a[0] >= MARGIN and a[1] >= MARGIN and a[2] <= inner_right and a[3] <= inner_bottom
        for b in boxes[i + 1 :]:
            assert not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]), (a, b)
    plain = render_level(scene, r.level_name, scale=scale)
    one = [names[pick % len(names)]]
    placed_one = name_boxes(scene, r.level_name, one, scale=scale)
    assume(placed_one)  # 單獨一個名稱也可能比 level 大，縮到最小還放不下就不寫
    labelled = render_level(scene, r.level_name, scale=scale, labels=one)
    assert (
        labelled.tobytes() == render_level(scene, r.level_name, scale=scale, labels=one).tobytes()
    )
    box = ImageChops.difference(plain.convert("RGB"), labelled.convert("RGB")).getbbox()
    assert box is not None
    left, top, right, bottom = box  # right、bottom 不含
    # 畫出來的字落在 name_boxes 說的範圍裡
    expected_box = placed_one[0][3]
    assert left >= expected_box[0] - 1 and top >= expected_box[1] - 1
    assert right <= expected_box[2] + 1 and bottom <= expected_box[3] + 1
    assert left >= MARGIN and top >= MARGIN and right <= inner_right and bottom <= inner_bottom
    # 放得下時以標示位置為中心（往內推過的就對齊推到的那一邊）
    _, x, y = one[0]
    cell = 16 * scale
    for low, high, start, end, at in (
        (left, right, MARGIN, inner_right, MARGIN + x * cell),
        (top, bottom, MARGIN, inner_bottom, MARGIN + y * cell),
    ):
        half = (high - low) / 2
        expected = min(max(at, start + half), end - half)
        assert abs((low + high) / 2 - expected) <= cell / 2 + 2
    # 淺色字加深色描邊：只看真的被畫過的像素，底下的地圖不算
    changed = ImageChops.difference(plain.convert("L"), labelled.convert("L"))
    # 有變就是 255；Pillow 的 stub 在 point 的一個 overload 帶 Unknown，只在這一行放寬
    changed = changed.point([0] + [255] * 255)  # pyright: ignore[reportUnknownMemberType]
    drawn = Image.new("L", labelled.size, 255)
    drawn.paste(labelled.convert("L"), mask=changed)
    darkest = cast(tuple[int, int], drawn.getextrema())[0]
    drawn = Image.new("L", labelled.size, 0)
    drawn.paste(labelled.convert("L"), mask=changed)
    lightest = cast(tuple[int, int], drawn.getextrema())[1]
    assert lightest >= 200 and darkest <= 60


def test_example_long_name_at_the_corner_stays_inside() -> None:
    """名稱比區域寬、區域又貼著 level 的角落時，名稱往內推，不跑出 level、不蓋到座標。"""
    r = one_level_rules(
        0,
        "L",
        (12, 8),
        SIMPLE_KINDS,
        (
            AreaRule("a_rather_long_name", "k", (1, 1), at=(0, 0)),
            AreaRule("another_long_name", "k", (1, 1), at=(11, 7)),
        ),
        (),
        (),
        grounds=SIMPLE_GROUNDS,
    )
    with tempfile.TemporaryDirectory() as tmp:
        scene, placed = generate_scene(r, Path(tmp) / "scene.ldtk")
    names = name_positions(placed, {a.name for a in r.areas})
    level = scene.level("L")
    for label in names:  # 左上角、右下角各一個；倍率 1 時字比格子高，上下也要往內推
        plain = render_level(scene, "L", scale=1)
        labelled = render_level(scene, "L", scale=1, labels=[label])
        box = ImageChops.difference(plain.convert("RGB"), labelled.convert("RGB")).getbbox()
        assert box is not None
        assert box[0] >= MARGIN and box[1] >= MARGIN, label
        assert box[2] <= MARGIN + level["pxWid"] and box[3] <= MARGIN + level["pxHei"], label
        # 超出圖的部分會被切掉、量不到；改比字的大小：跟寫在 level 中間時一樣大，才是沒被切
        middle = render_level(scene, "L", scale=1, labels=[(label[0], 6, 4)])
        free = ImageChops.difference(plain.convert("RGB"), middle.convert("RGB")).getbbox()
        assert free is not None
        assert (box[2] - box[0], box[3] - box[1]) == (free[2] - free[0], free[3] - free[1]), label


def test_example_name_does_not_cover_an_entity_on_it() -> None:
    """entity 擺在區域正中央、名稱也寫在那裡時，entity 方塊照樣看得見（RENDER-3）。"""
    r = one_level_rules(
        0,
        "L",
        (10, 10),
        SIMPLE_KINDS,
        (AreaRule("plaza", "k", (3, 3), at="center"),),
        (),
        (EntityRule("spawn", "PlayerSpawn", "plaza.center"),),
        grounds=SIMPLE_GROUNDS,
    )
    with tempfile.TemporaryDirectory() as tmp:
        scene, placed = generate_scene(r, Path(tmp) / "scene.ldtk")
    image = render_level(scene, "L", labels=name_positions(placed, {a.name for a in r.areas}))
    x, y = placed.entities["spawn"]
    center = (MARGIN + x * 32 + 16, MARGIN + y * 32 + 16)
    assert image.getpixel(center) == _hex(scene.project["defs"]["entities"][0]["color"])


def test_example_name_wider_than_the_level() -> None:
    """名稱比 level 寬：縮小字寫得下就寫；縮到最小還放不下就不寫，不跑出 level。"""
    r = one_level_rules(
        0,
        "L",
        (2, 1),
        SIMPLE_KINDS,
        (
            AreaRule("medium", "k", (1, 1), at=(0, 0)),
            AreaRule("a_name_far_too_long_for_two_cells", "k", (1, 1), at=(1, 0)),
        ),
        (),
        (),
        grounds=SIMPLE_GROUNDS,
    )
    with tempfile.TemporaryDirectory() as tmp:
        scene, placed = generate_scene(r, Path(tmp) / "scene.ldtk")
    names = name_positions(placed, {a.name for a in r.areas})
    drawn = [n for n, _, _, _ in name_boxes(scene, "L", names, scale=1)]
    assert drawn == ["medium"]
    plain = render_level(scene, "L", scale=1)
    labelled = render_level(scene, "L", scale=1, labels=names)
    box = ImageChops.difference(plain.convert("RGB"), labelled.convert("RGB")).getbbox()
    assert box is not None
    assert box[0] >= MARGIN and box[2] <= MARGIN + 32


def test_example_neighbour_names_do_not_overlap() -> None:
    """兩個相鄰的小區域、名稱都比區域寬：後擺的縮小字或不寫，不疊在先擺的上面。"""
    r = one_level_rules(
        0,
        "L",
        (6, 3),
        SIMPLE_KINDS,
        (
            AreaRule("north_gate", "k", (1, 1), at=(2, 1)),
            AreaRule("north_road", "k", (1, 1), at=(3, 1)),
        ),
        (),
        (),
        grounds=SIMPLE_GROUNDS,
    )
    with tempfile.TemporaryDirectory() as tmp:
        scene, placed = generate_scene(r, Path(tmp) / "scene.ldtk")
    boxes = name_boxes(scene, "L", name_positions(placed, {a.name for a in r.areas}))
    assert boxes[0][0] == "north_gate"
    if len(boxes) == 2:
        a, b = boxes[0][3], boxes[1][3]
        assert not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])


def test_example_axis_numbers_stay_on_top(tmp_path: Path) -> None:
    """entity 跑進上方的邊框時，格子座標照樣看得見（座標最後畫）。"""
    scene = _build(tmp_path, 4, 4, [], [])
    plain = render_level(scene, "Room")
    place_entity(scene, "Room", "PlayerSpawn", x=0, y=0)
    layers = scene.level("Room")["layerInstances"] or []
    entity = next(e for ly in layers for e in ly["entityInstances"])
    entity["px"] = [0, -10]  # 方塊往上跑進座標那一條
    covered = render_level(scene, "Room")
    text = (230, 230, 230, 255)
    for x in range(MARGIN, MARGIN + 32):
        for y in range(MARGIN):
            if plain.getpixel((x, y)) == text:
                assert covered.getpixel((x, y)) == text, (x, y)


def test_example_record_with_nan_position_is_skipped() -> None:
    """生成紀錄裡的位置是 NaN 或 Infinity（JSON 讀得進來）時略過，不讓 render 當掉。"""
    saved = {"labels": {"L": [["a", float("nan"), 1], ["b", 1, float("inf")], ["c", 1, 1]]}}
    assert record.labels(saved, "L") == [("c", 1.0, 1.0)]


@pytest.mark.law("RENDER-6")
@settings(max_examples=30, deadline=None)
@given(
    placeable_multi_village_rules(min_portals=1),
    st.sampled_from(["Free", "LinearHorizontal", "LinearVertical"]),
)
def test_render_6_world_overview(r: Rules, arrangement: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        scene, _ = generate_scene(r, Path(tmp) / "scene.ldtk")
    levels = scene.project["levels"]
    # 線性排法：LDtk 把世界座標存成 -1，照 level 的順序接著排（這裡獨立另算一次）
    at = {lv["identifier"]: (lv["worldX"], lv["worldY"]) for lv in levels}
    if arrangement != "Free":
        scene.project["worldLayout"] = arrangement
        offset = 0
        for lv in levels:
            horizontal = arrangement == "LinearHorizontal"
            at[lv["identifier"]] = (offset, 0) if horizontal else (0, offset)
            offset += lv["pxWid"] if horizontal else lv["pxHei"]
            lv["worldX"], lv["worldY"] = -1, -1
    image = render_world(scene)
    assert image.tobytes() == render_world(scene).tobytes()  # 同一份場景地圖，位元組相同
    left = min(x for x, _ in at.values())
    top = min(y for _, y in at.values())
    span = max(
        max(at[lv["identifier"]][0] + lv["pxWid"] for lv in levels) - left,
        max(at[lv["identifier"]][1] + lv["pxHei"] for lv in levels) - top,
    )
    scale = min(1.0, 1600 / span)
    frame = (24, 26, 34, 255)
    rects = world_view(scene).rects
    for level in levels:  # 照世界座標等比例縮小，左上角留邊框
        x, y, w, h = rects[level["identifier"]]
        wx, wy = at[level["identifier"]]
        assert (x, y) == (MARGIN + round((wx - left) * scale), MARGIN + round((wy - top) * scale))
        assert (w, h) == (
            max(1, round(level["pxWid"] * scale)),
            max(1, round(level["pxHei"] * scale)),
        )
        background = sum(
            1 for i in range(w) for j in range(h) if image.getpixel((x + i, y + j)) == frame
        )
        assert background < 0.1 * w * h, level["identifier"]  # level 有畫出來，不是空白
        # IntGrid 有畫出來：上面三層都沒畫、也沒被 entity 方塊蓋到的格，
        # Ground 用到的值，顏色出現在它的範圍裡
        by_name = {ly["__identifier"]: ly["intGridCsv"] for ly in level["layerInstances"] or []}
        above = zip(by_name["Collision"], by_name["Slope"], by_name["Height"], strict=True)
        cols = level["pxWid"] // 16
        covered = {
            (cx, cy)
            for ly in level["layerInstances"] or []
            for e in ly["entityInstances"]
            for cx in range(e["__grid"][0] - 1, e["__grid"][0] + e["width"] // 16 + 1)
            for cy in range(e["__grid"][1] - 1, e["__grid"][1] + e["height"] // 16 + 1)
        }
        bare = [
            i
            for i, cells in enumerate(above)
            if not any(cells) and (i % cols, i // cols) not in covered
        ]
        used = {by_name["Ground"][i] for i in bare} - {0}
        colors = {_hex(v.color) for v in r.grounds if v.value in used}
        seen = {image.getpixel((x + i, y + j)) for i in range(w) for j in range(h)}
        assert not colors or colors & seen, level["identifier"]
        # entity 方塊也畫出來（倍率夠大、方塊比線的端點大的時候才看得到）
        if scale >= 0.5:
            types = {
                _hex(d["color"])
                for d in scene.project["defs"]["entities"]
                for ly in level["layerInstances"] or []
                for e in ly["entityInstances"]
                if e["defUid"] == d["uid"]
            }
            assert types <= seen, level["identifier"]
    # 每一對互指的 EntityRef（傳送點）畫一條線：兩端換成總覽座標後，中點附近是線的顏色
    links = world_links(scene)
    assert len(links) == len(r.portals)
    for start, end in links:
        (x0, y0), (x1, y1) = (
            (MARGIN + round((px - left) * scale), MARGIN + round((py - top) * scale))
            for px, py in (start, end)
        )
        mx, my = (x0 + x1) // 2, (y0 + y1) // 2
        around = [
            cast(tuple[int, ...], image.getpixel((mx + dx, my + dy)))[:3]
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
        ]
        assert (255, 214, 0) in around, (mx, my)


def _two_rooms(tmp_path: Path) -> SceneMap:
    scene = new_scene(tmp_path / "scene.ldtk")
    scene.project["levels"].clear()
    add_level(scene, "Village", width_cells=8, height_cells=4)
    add_level(scene, "Inn", width_cells=3, height_cells=2, world_x=200, world_y=0)
    return scene


def test_example_linear_layout_lines_levels_up(tmp_path: Path) -> None:
    """LDtk 的線性排法把世界座標存成 -1：照 level 的順序接著排，不疊成一團。"""
    scene = _two_rooms(tmp_path)
    scene.project["worldLayout"] = "LinearHorizontal"
    for level in scene.project["levels"]:
        level["worldX"], level["worldY"] = -1, -1
    rects = world_view(scene).rects
    assert rects["Inn"][0] == rects["Village"][0] + rects["Village"][2]
    assert rects["Inn"][1] == rects["Village"][1]


def test_example_links_follow_layer_offsets_and_one_way_refs(tmp_path: Path) -> None:
    """連線的端點算進圖層定義的偏移（跟 render 一樣）；單向的引用也畫線。"""
    scene = _two_rooms(tmp_path)
    add_entity_def(scene, "Sign", field_types={"to": "EntityRef"})
    target = place_entity(scene, "Inn", "Sign", x=0, y=0)["iid"]
    place_entity(scene, "Village", "Sign", x=0, y=0, values={"to": target})
    plain = world_links(scene)
    assert len(plain) == 1  # 單向也算一條
    entities = next(d for d in scene.project["defs"]["layers"] if d["type"] == "Entities")
    entities["pxOffsetX"] = 16
    moved = world_links(scene)
    assert [round(a[0] - b[0]) for a, b in zip(moved[0], plain[0], strict=True)] == [16, 16]


def test_example_small_level_name_stays_inside(tmp_path: Path) -> None:
    """level 縮得很小時，名稱縮字或不寫，不蓋到旁邊的 level。"""
    scene = new_scene(tmp_path / "scene.ldtk")
    scene.project["levels"].clear()
    add_level(scene, "A_very_long_level_name", width_cells=2, height_cells=2)
    add_level(scene, "Huge", width_cells=400, height_cells=10, world_x=40, world_y=0)
    image = render_world(scene)
    x, y, w, h = world_view(scene).rects["A_very_long_level_name"]
    huge = world_view(scene).rects["Huge"]
    gap = {  # 兩個 level 之間的空地
        image.getpixel((gx, gy)) for gx in range(x + w, huge[0]) for gy in range(y, y + h)
    }
    assert gap <= {(24, 26, 34, 255)}


def test_example_translucent_layer_blends_with_the_layer_below(tmp_path: Path) -> None:
    """曾經壞過：半透明的 Height 層要跟下面的 Ground 混色，不是蓋掉成半透明像素（D-011）。"""
    scene = new_scene(tmp_path / "village.ldtk")
    paint_cells(scene, "Level_0", cells=[(2, 2)], value=1, layer="Ground")
    paint_cells(scene, "Level_0", cells=[(2, 2), (3, 2)], value=1, layer="Height")
    defs = {d["identifier"]: d for d in scene.project["defs"]["layers"]}
    ground = _hex(defs["Ground"]["intGridValues"][0]["color"])
    height = _hex(defs["Height"]["intGridValues"][0]["color"])
    alpha = defs["Height"]["displayOpacity"]
    assert 0 < alpha < 1
    image = render_level(scene, "Level_0", scale=1)
    background = _hex(scene.level("Level_0")["__bgColor"])
    row = MARGIN + 2 * 16 + 8

    def mix(top: tuple[int, int, int, int], bottom: tuple[int, int, int, int]) -> tuple[int, ...]:
        return tuple(
            round(t * alpha + b * (1 - alpha)) for t, b in zip(top[:3], bottom[:3], strict=True)
        )

    def near(pixel: object, expected: tuple[int, ...]) -> bool:
        # 透明度存成 0～255 的整數，混色差 1 以內算對
        got = cast(tuple[int, ...], pixel)
        return got[3] == 255 and all(
            abs(g - e) <= 1 for g, e in zip(got[:3], expected, strict=True)
        )

    assert near(image.getpixel((MARGIN + 2 * 16 + 8, row)), mix(height, ground))
    assert near(image.getpixel((MARGIN + 3 * 16 + 8, row)), mix(height, background))


def test_example_translucent_layer_blends_once_in_world_overview(tmp_path: Path) -> None:
    """曾經壞過：總覽縮小時好幾格落在同一個像素，半透明的 Height 層不能疊混好幾次（#48 審查）。"""
    scene = new_scene(tmp_path / "wide.ldtk")
    # 2000 格寬：總覽縮到一格不到一個像素，好幾格疊在同一個像素上
    add_level(scene, "Wide", width_cells=2000, height_cells=40)
    cells = [(x, y) for x in range(2000) for y in range(40)]
    paint_cells(scene, "Wide", cells=cells, value=1, layer="Ground")
    paint_cells(scene, "Wide", cells=cells, value=1, layer="Height")
    view = world_view(scene)
    assert view.scale < 1 / 16
    defs = {d["identifier"]: d for d in scene.project["defs"]["layers"]}
    ground = _hex(defs["Ground"]["intGridValues"][0]["color"])
    height = _hex(defs["Height"]["intGridValues"][0]["color"])
    alpha = defs["Height"]["displayOpacity"]
    expected = [
        round(t * alpha + b * (1 - alpha)) for t, b in zip(height[:3], ground[:3], strict=True)
    ]
    x, y, w, h = view.rects["Wide"]
    got = cast(tuple[int, ...], render_world(scene).getpixel((x + w // 2, y + h - 2)))
    assert all(abs(g - e) <= 1 for g, e in zip(got[:3], expected, strict=True))


def test_example_area_name_avoids_its_part(tmp_path: Path) -> None:
    """曾經壞過（#62 審查）：帶零件的區域，零件擺在中心；區域名稱挪開，不被零件方塊蓋住。"""
    kinds = (Kind("house", ground="g", part="house"),)
    areas = (AreaRule("house_1", "house", (5, 4), at="center"),)
    r = one_level_rules(1, "L", (12, 12), kinds, areas, (), (), SIMPLE_GROUNDS)
    scene, placed = generate_scene(r, tmp_path / "scene.ldtk")
    names = name_positions(placed, {a.name for a in r.areas})
    ((name, _, _, box),) = name_boxes(scene, "L", names, scale=2)
    assert name == "house_1"
    x, y = placed.parts["house_1"]
    part = (MARGIN + x * 32, MARGIN + y * 32 - 14, MARGIN + (x + 1) * 32, MARGIN + (y + 1) * 32)
    assert not (box[0] < part[2] and part[0] < box[2] and box[1] < part[3] and part[1] < box[3])
