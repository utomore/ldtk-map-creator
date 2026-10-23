"""照規則生成場景地圖（D-007）：配置之後畫進一個 level。

同規則同 seed，寫出的檔完全相同：iid 與圖層的 seed 都由 seed 加名字推出。
"""

import random
from pathlib import Path

from .derived import refresh_derived
from .edit import paint_cells, place_entity
from .errors import SceneError
from .ids import derived_iid
from .layout import Layout, layout
from .rules import Rules
from .scene import SceneMap, add_level, new_scene


def element_iid(seed: int, name: str) -> str:
    """元素產生的 entity 的 iid。"""
    return derived_iid(seed, f"element:{name}")


def generate_scene(rules: Rules, path: Path, *, split: bool = True) -> tuple[SceneMap, Layout]:
    """照規則建一個新的場景地圖（還沒寫檔），回傳場景地圖與配置結果。"""
    placed = layout(rules)
    scene = new_scene(path, split=split)
    project = scene.project
    intgrid = [d for d in project["defs"]["layers"] if d["type"] == "IntGrid"]
    if len(intgrid) != 1:
        raise SceneError(f"範本要剛好一個 IntGrid 圖層，現在有 {len(intgrid)} 個")
    intgrid[0]["intGridValues"] = [
        {"value": k.value, "identifier": k.name, "color": k.color, "tile": None, "groupUid": 0}
        for k in rules.kinds
    ]
    project["levels"].clear()  # 範本附的 Level_0
    width, height = rules.level_size
    level = add_level(scene, rules.level_name, width_cells=width, height_cells=height)
    name = rules.level_name

    value = {k.name: k.value for k in rules.kinds}
    kind = {e.name: e.kind for e in (*rules.areas, *rules.paths)}
    for element, cells in placed.cells.items():
        paint_cells(scene, name, cells=sorted(cells), value=value[kind[element]])
    for entity in rules.entities:
        x, y = placed.entities[entity.name]
        try:
            instance = place_entity(scene, name, entity.type, x=x, y=y)
        except SceneError as error:
            raise SceneError(f"{entity.name}.type：{error}") from error
        if instance["iid"] in {element_iid(rules.seed, e.name) for e in rules.entities}:
            # 碰到 maxCount 被挪走的是先前擺的 entity
            raise SceneError(f"{entity.name}.type：{entity.type} 的數量到上限了")
        instance["iid"] = element_iid(rules.seed, entity.name)

    project["iid"] = derived_iid(rules.seed, "project")
    project["dummyWorldIid"] = derived_iid(rules.seed, "world")
    level["iid"] = derived_iid(rules.seed, f"level:{name}")
    defs = {d["uid"]: d["identifier"] for d in project["defs"]["layers"]}
    for layer in level["layerInstances"] or []:
        key = f"layer:{name}:{defs[layer['layerDefUid']]}"
        layer["iid"] = derived_iid(rules.seed, key)
        layer["seed"] = random.Random(f"{rules.seed}:{key}").randrange(10**6)
    refresh_derived(project)
    return scene, placed
