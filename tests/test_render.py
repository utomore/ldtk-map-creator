"""RENDER-1～RENDER-4：預覽圖。"""

import copy
import io
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image

from ldtk_map_creator.core.edit import paint_cells, place_entity
from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.render import MARGIN, image_size, render_level, save_preview
from ldtk_map_creator.core.scene import SceneMap, add_level, load_scene, new_scene, save_scene

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
    paint_cells(scene, "Room", cells=[(x, y) for x, y in painted if x < w and y < h], value=1)
    for x, y in spawns:
        if x < w and y < h:
            place_entity(scene, "Room", "Spawn", x=x, y=y)
    return scene


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
        wall = _hex(defs["layers"][0]["intGridValues"][0]["color"])
        spawn = _hex(defs["entities"][0]["color"])
        background = _hex(level["__bgColor"])
        cell = 16 * scale
        placed = {(x, y) for x, y in spawns if x < w and y < h}
        # entity 名稱寫在方塊上方（第 0 列寫在下方），可能比一格寬：那一列整列都不驗
        label_rows = {y - 1 if y > 0 else y + 1 for _, y in placed}
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


def test_example_save_preview_location(tmp_path: Path) -> None:
    """典型：預覽圖存在場景地圖旁的 previews/<主檔名>/<level>.png。"""
    scene = new_scene(tmp_path / "village.ldtk")
    path = save_preview(scene, "Level_0")
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
    entity = place_entity(scene, "Level_0", "Spawn", x=2, y=2)
    entity["width"] = 48
    layer = (scene.level("Level_0")["layerInstances"] or [])[1]
    layer["pxOffsetX"] = 8
    image = render_level(scene, "Level_0", scale=1)
    spawn = _hex(scene.project["defs"]["entities"][0]["color"])
    # 方塊從 x = 2*16 + 8 起 48px；取右段、避開格線的一點
    assert image.getpixel((MARGIN + 32 + 8 + 44, MARGIN + 32 + 8)) == spawn
    assert image.getpixel((MARGIN + 32 + 4, MARGIN + 32 + 8)) != spawn


def test_example_hidden_layer_is_not_drawn(tmp_path: Path) -> None:
    """邊界：LDtk 裡隱藏的圖層不畫。"""
    scene = new_scene(tmp_path / "village.ldtk")
    paint_cells(scene, "Level_0", cells=[(3, 3)], value=1)
    (scene.level("Level_0")["layerInstances"] or [])[0]["visible"] = False
    image = render_level(scene, "Level_0", scale=1)
    background = _hex(scene.level("Level_0")["__bgColor"])
    assert image.getpixel((MARGIN + 3 * 16 + 8, MARGIN + 3 * 16 + 8)) == background


def test_example_undefined_value_is_scene_error(tmp_path: Path) -> None:
    """邊界：csv 裡有定義沒有的值，丟 SceneError 而不是 KeyError。"""
    scene = new_scene(tmp_path / "village.ldtk")
    (scene.level("Level_0")["layerInstances"] or [])[0]["intGridCsv"][0] = 7
    with pytest.raises(SceneError):
        render_level(scene, "Level_0")


def test_example_stacked_entities_keep_their_color(tmp_path: Path) -> None:
    """曾經壞過：上下相鄰的 entity，下面那個的名稱不能蓋住上面那個方塊。"""
    scene = new_scene(tmp_path / "village.ldtk")
    place_entity(scene, "Level_0", "Spawn", x=2, y=1)
    place_entity(scene, "Level_0", "Spawn", x=2, y=2)
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
        save_preview(scene, "Level_0")
    assert list((tmp_path / "previews" / "village").glob("*.tmp")) == []


def test_example_grid_follows_visible_intgrid_offset(tmp_path: Path) -> None:
    """曾經壞過：格線跟著看得見的 IntGrid 的偏移，格子才對得上格線。"""
    scene = new_scene(tmp_path / "village.ldtk")
    (scene.level("Level_0")["layerInstances"] or [])[0]["pxOffsetX"] = 8
    image = render_level(scene, "Level_0", scale=1)
    background = _hex(scene.level("Level_0")["__bgColor"])
    row = MARGIN + 8
    assert image.getpixel((MARGIN + 16 + 8, row)) != background  # 偏移後的格線
    assert image.getpixel((MARGIN + 16, row)) == background  # 沒偏移的位置不畫線


def test_example_bad_color_and_zero_size_are_scene_errors(tmp_path: Path) -> None:
    """邊界：顏色不是十六進位、entity 尺寸是 0，都丟 SceneError。"""
    scene = new_scene(tmp_path / "village.ldtk")
    scene.project["defs"]["entities"][0]["color"] = "#GG0000"
    place_entity(scene, "Level_0", "Spawn", x=0, y=0)
    with pytest.raises(SceneError):
        render_level(scene, "Level_0")
    scene.project["defs"]["entities"][0]["color"] = "#BE4A2F"
    entity = (scene.level("Level_0")["layerInstances"] or [])[1]["entityInstances"][0]
    entity["width"] = 0
    with pytest.raises(SceneError):
        render_level(scene, "Level_0")


def test_example_label_in_one_row_level_stays_inside(tmp_path: Path) -> None:
    """邊界：只有一列高的 level，名稱寫進方塊裡，不跑出圖外。"""
    scene = new_scene(tmp_path / "village.ldtk")
    add_level(scene, "Strip", width_cells=6, height_cells=1, world_x=4096)
    place_entity(scene, "Strip", "Spawn", x=0, y=0)
    image = render_level(scene, "Strip", scale=2)
    box = image.crop((MARGIN, MARGIN, MARGIN + 32, MARGIN + 32))
    # 不含外框；字有反鋸齒，找比方塊顏色亮很多的像素（方塊的綠色分量是 0x4A）
    inside = [box.getpixel((x, y)) for x in range(2, 30) for y in range(2, 30)]
    assert any(isinstance(p, tuple) and p[1] > 150 for p in inside)
