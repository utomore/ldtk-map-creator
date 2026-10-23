"""產生隨機場景地圖的 hypothesis 策略。"""

from dataclasses import dataclass
from pathlib import Path

from hypothesis import strategies as st

from ldtk_map_creator.core.scene import SceneMap, add_level, new_scene


@dataclass(frozen=True)
class SceneSpec:
    split: bool
    levels: tuple[tuple[int, int, int, int], ...]  # 寬格、高格、worldX、worldY


scene_specs = st.builds(
    SceneSpec,
    split=st.booleans(),
    levels=st.lists(
        st.tuples(
            st.integers(1, 64),
            st.integers(1, 64),
            st.integers(-4096, 4096),
            st.integers(-4096, 4096),
        ),
        max_size=4,
    ).map(tuple),
)


def build_scene(spec: SceneSpec, folder: Path) -> SceneMap:
    scene = new_scene(folder / "scene.ldtk", split=spec.split)
    for i, (w, h, x, y) in enumerate(spec.levels, start=1):
        add_level(scene, f"Room_{i}", width_cells=w, height_cells=h, world_x=x, world_y=y)
    return scene
