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


def generate_scene(
    rules: Rules, path: Path, *, split: bool = True, overwrite: bool = False
) -> tuple[SceneMap, Layout]:
    """照規則建一個新的場景地圖（還沒寫檔），回傳場景地圖與配置結果。

    `overwrite=True` 時磁碟上已經有這個場景地圖也照樣建，寫檔時蓋掉（重新生成）。
    """
    placed = layout(rules)
    scene = new_scene(path, split=split, overwrite=overwrite)
    project = scene.project
    for layer_type in ("IntGrid", "Entities"):
        found = [d for d in project["defs"]["layers"] if d["type"] == layer_type]
        if len(found) != 1:
            raise SceneError(f"範本要剛好一個 {layer_type} 圖層，現在有 {len(found)} 個")
    intgrid = [d for d in project["defs"]["layers"] if d["type"] == "IntGrid"]
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
        if kind[element] not in value:
            raise SceneError(f"{element}.kind：[kinds] 裡沒有 {kind[element]}")
        paint_cells(scene, name, cells=sorted(cells), value=value[kind[element]])
    entities_def = next(d["uid"] for d in project["defs"]["layers"] if d["type"] == "Entities")
    layers = level["layerInstances"] or []
    entity_layer = next(ly for ly in layers if ly["layerDefUid"] == entities_def)
    for entity in rules.entities:
        x, y = placed.entities[entity.name]
        before = len(entity_layer["entityInstances"])
        try:
            instance = place_entity(scene, name, entity.type, x=x, y=y)
        except SceneError as error:
            # 位置由配置器保證在 level 內、圖層上面檢查過；剩下會失敗的都出在 entity 定義
            raise SceneError(f"{entity.name}.type：{error}") from error
        if len(entity_layer["entityInstances"]) != before + 1:
            # 到了 maxCount，先前擺的 entity 被挪走或丟掉
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
