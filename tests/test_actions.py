"""SCENE-5：操作完成後，磁碟上的場景地圖與預覽圖都是最新的；失敗時磁碟不變。"""

import tempfile
import threading
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image

from ldtk_map_creator.core import actions
from ldtk_map_creator.core import render as rendering
from ldtk_map_creator.core.edit import read_cell
from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.render import preview_path, render_level
from ldtk_map_creator.core.scene import SceneMap, load_scene, save_scene

FAST = settings(max_examples=25, deadline=None)

coord = st.integers(-1, 16)
steps = st.lists(
    st.one_of(
        st.tuples(st.just("paint"), coord, coord, st.integers(1, 4), st.sampled_from([0, 1, 2])),
        st.tuples(st.just("place"), coord, coord, st.just(1), st.just(0)),
        st.tuples(st.just("move"), coord, coord, st.just(1), st.just(0)),
        st.tuples(st.just("delete"), st.just(0), st.just(0), st.just(1), st.just(0)),
    ),
    max_size=6,
)


def _files(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.mark.law("SCENE-5")
@FAST
@given(steps)
def test_scene_5_actions_keep_disk_and_preview_current(
    plan: list[tuple[str, int, int, int, int]],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        path = folder / "village.ldtk"
        actions.create_scene(path)
        placed: list[str] = []
        for kind, x, y, size, value in plan:
            before = _files(folder)
            try:
                if kind == "paint":
                    actions.paint_rect(
                        path,
                        "Level_0",
                        x=x,
                        y=y,
                        width=size,
                        height=size,
                        value=value,
                        layer="Collision",
                    )
                elif kind == "place":
                    result = actions.place_entity(path, "Level_0", "PlayerSpawn", x=x, y=y)
                    assert result.iid is not None
                    placed.append(result.iid)
                elif kind == "move" and placed:
                    actions.move_entity(path, placed[-1], x=x, y=y)
                elif kind == "delete" and placed:
                    actions.delete_entity(path, placed.pop())
                else:
                    continue
            except SceneError:
                assert _files(folder) == before
                continue
            scene = load_scene(path)
            _check_disk(scene, kind, x, y, size, value, placed)
            expected = render_level(scene, "Level_0")
            with Image.open(preview_path(scene, "Level_0")) as saved:
                assert saved.tobytes() == expected.tobytes()


def _check_disk(
    scene: SceneMap, kind: str, x: int, y: int, size: int, value: int, placed: list[str]
) -> None:
    """磁碟上的場景地圖就是操作後的狀態。"""
    if kind == "paint":
        for cy in range(y, y + size):
            for cx in range(x, x + size):
                assert read_cell(scene, "Level_0", x=cx, y=cy, layer="Collision") == value
        return
    entities = {
        e["iid"]: e
        for layer in scene.level("Level_0")["layerInstances"] or []
        for e in layer["entityInstances"]
    }
    if kind in ("place", "move"):
        assert entities[placed[-1]]["__grid"] == [x, y]
    assert set(entities) == set(placed)


def test_example_failed_preview_leaves_disk_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """曾經壞過：地圖寫好了、預覽圖寫失敗，要把地圖也還原，免得 agent 重試時畫兩次。"""
    path = tmp_path / "village.ldtk"
    actions.create_scene(path)
    before = _files(tmp_path)

    def broken(*_: object) -> Path:
        raise SceneError("disk full")

    monkeypatch.setattr(rendering, "write_preview", broken)
    with pytest.raises(SceneError):
        actions.paint_rect(path, "Level_0", x=0, y=0, value=1, layer="Collision")
    assert _files(tmp_path) == before


def test_example_move_last_one_across_levels_updates_both_previews(tmp_path: Path) -> None:
    """曾經壞過：PerWorld 的 MoveLastOne 從別的 level 移過來，兩個 level 的預覽圖都要更新。"""
    path = tmp_path / "village.ldtk"
    actions.create_scene(path)
    actions.add_level(path, "Inn", width_cells=8, height_cells=8, world_x=4096)
    scene = load_scene(path)
    spawn = scene.project["defs"]["entities"][0]
    spawn.update(maxCount=1, limitScope="PerWorld", limitBehavior="MoveLastOne")
    save_scene(scene)
    actions.place_entity(path, "Level_0", "PlayerSpawn", x=1, y=1)
    result = actions.place_entity(path, "Inn", "PlayerSpawn", x=2, y=2)
    assert {p.name for p in result.previews} == {"Level_0.png", "Inn.png"}


def test_example_only_changed_level_files_are_rewritten(tmp_path: Path) -> None:
    """效能與安全：只重寫有變的 level 檔；沒變的 level 檔原樣不動。"""
    path = tmp_path / "village.ldtk"
    actions.create_scene(path)
    actions.add_level(path, "Inn", width_cells=8, height_cells=8, world_x=4096)
    inn = tmp_path / "village" / "Inn.ldtkl"
    marked = inn.read_text(encoding="utf-8") + chr(10) * 2  # 內容一樣、多了空行
    inn.write_text(marked, encoding="utf-8")
    actions.paint_rect(path, "Level_0", x=0, y=0, value=1, layer="Collision")
    assert inn.read_text(encoding="utf-8") == marked


def test_example_parallel_actions_do_not_lose_updates(tmp_path: Path) -> None:
    """曾經壞過：MCP 會同時跑多個工具；同時畫不同格，每一格都要留下來。"""
    path = tmp_path / "village.ldtk"
    actions.create_scene(path)
    cells = [(x, 5) for x in range(12)]
    threads = [
        threading.Thread(
            target=actions.paint_rect,
            args=(path, "Level_0"),
            kwargs={"x": x, "y": y, "value": 1, "layer": "Collision"},
        )
        for x, y in cells
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    scene = load_scene(path)
    assert all(read_cell(scene, "Level_0", x=x, y=y, layer="Collision") == 1 for x, y in cells)


def test_example_no_change_still_returns_preview(tmp_path: Path) -> None:
    """邊界：畫一格本來就是這個值，內容沒變，也照樣回傳目標 level 的預覽圖。"""
    path = tmp_path / "village.ldtk"
    actions.create_scene(path)
    actions.paint_rect(path, "Level_0", x=0, y=0, value=1, layer="Collision")
    again = actions.paint_rect(path, "Level_0", x=0, y=0, value=1, layer="Collision")
    assert [p.name for p in again.previews] == ["Level_0.png"]


def test_example_neighbour_only_change_is_not_redrawn(tmp_path: Path) -> None:
    """效能：新 level 貼在旁邊只改了 Level_0 的相鄰關係，不重畫 Level_0 的預覽圖。"""
    path = tmp_path / "village.ldtk"
    actions.create_scene(path)
    result = actions.add_level(path, "East", width_cells=4, height_cells=4, world_x=256)
    assert [p.name for p in result.previews] == ["East.png", "world-overview.png"]
    neighbours = load_scene(path).level("Level_0")["__neighbours"]
    assert neighbours != []  # Level_0 的檔照樣更新了相鄰關係
