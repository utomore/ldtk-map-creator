"""GEN-5、GEN-6：照規則生成的操作，與「被直接改過就擋下來」（D-008）。"""

import shutil
import tempfile
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from PIL import Image

from ldtk_map_creator.core import actions, record
from ldtk_map_creator.core.edit import read_cell
from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.render import preview_path, render_level
from ldtk_map_creator.core.rules import Rules
from ldtk_map_creator.core.scene import load_scene
from tests.rule_strategies import placeable_village_rules

FAST = settings(max_examples=40, deadline=None)
EXAMPLE = Path(__file__).parent / "data" / "village.rules.toml"


def _files(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def _write_rules(r: Rules, path: Path) -> None:
    """把規則寫成規則檔；只用到村莊策略會產生的欄位。"""
    lines = [f"seed = {r.seed}", "[level]", f'name = "{r.level_name}"']
    lines.append(f"size = [{r.level_size[0]}, {r.level_size[1]}]")
    lines.append("[kinds]")
    lines += [f'{k.name} = {{ value = {k.value}, color = "{k.color}" }}' for k in r.kinds]
    for a in r.areas:
        lines += ["[[area]]", f'name = "{a.name}"', f'kind = "{a.kind}"']
        lines.append(f"size = [{a.size[0]}, {a.size[1]}]")
        if a.at == "center":
            lines.append('at = "center"')
        elif a.at is not None:
            lines.append(f"at = [{a.at[0]}, {a.at[1]}]")
        else:
            lines += [f'near = "{a.near}"', f"gap = {a.gap}"]
            if a.side:
                lines.append(f'side = "{a.side}"')
    for p in r.paths:
        lines += ["[[path]]", f'name = "{p.name}"', f'kind = "{p.kind}"']
        lines += [f'from = "{p.start}"', f'to = "{p.to}"', f"width = {p.width}"]
    for e in r.entities:
        assert isinstance(e.at, str)
        lines += ["[[entity]]", f'name = "{e.name}"', f'type = "{e.type}"', f'at = "{e.at}"']
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.law("GEN-5")
@pytest.mark.law("SCENE-5")
@FAST
@given(placeable_village_rules())
def test_gen_5_record_matches_disk_and_regenerating_is_a_no_op(r: Rules) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        path = folder / "village.ldtk"
        _write_rules(r, record.rules_path(path))
        actions.generate_scene(path)
        saved = record.read_record(path)
        assert saved is not None
        on_disk = load_scene(path)
        assert saved["content"] == record.content(on_disk)
        with Image.open(preview_path(on_disk, r.level_name)) as image:
            assert image.tobytes() == render_level(on_disk, r.level_name).tobytes()
        before = _files(folder)
        result = actions.generate_scene(path)
        assert _files(folder) == before
        assert "沒有元素移動" in result.summary


# 一次直接修改：（種類、x、y）；x、y 拿來挑格子或 entity
edits = st.tuples(
    st.sampled_from(["paint", "place", "move", "delete", "level"]),
    st.integers(0, 99),
    st.integers(0, 99),
)


@pytest.mark.law("GEN-6")
@FAST
@given(placeable_village_rules(), edits, st.booleans())
def test_gen_6_direct_edit_blocks_regeneration_until_forced(
    r: Rules, change: tuple[str, int, int], force_twice: bool
) -> None:
    kind, a, b = change
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        path = folder / "village.ldtk"
        _write_rules(r, record.rules_path(path))
        actions.generate_scene(path)
        fresh = _files(folder)
        width, height = r.level_size
        x, y = a % width, b % height
        iids = [
            e["iid"]
            for ly in load_scene(path).level(r.level_name)["layerInstances"] or []
            for e in ly["entityInstances"]
        ]
        if kind == "paint":
            current = read_cell(load_scene(path), r.level_name, x=x, y=y)
            actions.paint_cells(path, r.level_name, cells=[(x, y)], value=(current + 1) % 4)
        elif kind == "place":
            actions.place_entity(path, r.level_name, "Spawn", x=x, y=y)
        elif kind in ("move", "delete"):
            assume(iids)
            iid = iids[a % len(iids)]
            if kind == "delete":
                actions.delete_entity(path, iid)
            else:
                grid = next(
                    e["__grid"]
                    for ly in load_scene(path).level(r.level_name)["layerInstances"] or []
                    for e in ly["entityInstances"]
                    if e["iid"] == iid
                )
                assume(tuple(grid) != (x, y))
                actions.move_entity(path, iid, x=x, y=y)
        else:
            actions.add_level(path, "Extra", width_cells=2, height_cells=2, world_x=4096)

        edited = _files(folder)
        with pytest.raises(SceneError, match="被直接改過") as error:
            actions.generate_scene(path)
        assert _files(folder) == edited
        assert "\n- " in str(error.value)  # 列出差異

        actions.generate_scene(path, force=True)
        if force_twice:
            actions.generate_scene(path)  # 蓋掉之後就不再擋
        after = _files(folder)
        # 蓋掉之後跟剛生成時一樣；只多了被改過時才有的預覽圖（例如新加的 level）
        assert {k: v for k, v in after.items() if k in fresh} == fresh
        assert set(after) - set(fresh) <= {str(Path("previews", "village", "Extra.png"))}


def test_example_one_line_change_moves_one_element(tmp_path: Path) -> None:
    """驗收：改一行規則重新生成，只有那個元素移動，摘要也只列它。"""
    path = tmp_path / "village.ldtk"
    rules_file = record.rules_path(path)
    shutil.copy(EXAMPLE, rules_file)
    actions.generate_scene(path)
    text = rules_file.read_text(encoding="utf-8")
    rules_file.write_text(text.replace('side = "east"', 'side = "west"'), encoding="utf-8")
    result = actions.generate_scene(path)
    assert "位置變了：house_2" in result.summary
    assert "新的" not in result.summary and "拿掉了" not in result.summary


def test_example_scene_without_record_is_not_overwritten(tmp_path: Path) -> None:
    """用 M1 的操作建的場景地圖，加了規則檔也不會被直接蓋掉：沒有紀錄就不知道改過什麼。"""
    path = tmp_path / "village.ldtk"
    actions.create_scene(path)
    shutil.copy(EXAMPLE, record.rules_path(path))
    before = _files(tmp_path)
    with pytest.raises(SceneError, match="沒有上次生成的紀錄"):
        actions.generate_scene(path)
    assert _files(tmp_path) == before
    actions.generate_scene(path, force=True)
    assert record.read_record(path) is not None
