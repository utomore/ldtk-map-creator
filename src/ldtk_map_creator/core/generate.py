"""照規則生成場景地圖（D-007、D-009、D-010）：配置之後畫進每個 level。

同規則同 seed，寫出的檔完全相同：iid 與圖層的 seed 都由 seed 加名字推出。
"""

import random
from pathlib import Path

from .derived import refresh_derived
from .edit import add_entity_def, paint_cells, place_entity
from .errors import SceneError
from .ids import derived_iid
from .layout import Layout, layout
from .model import Level
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
    # 傳送點的生成還沒做（#37）；先講清楚，不默默丟掉
    if rules.portals:
        raise SceneError(f"{rules.portals[0].name}.from：傳送點的生成還不支援（#37）")
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
    # 規則宣告的 entity 類型變成 LDtk 的 entity 定義，加在範本的 Spawn 旁邊（D-010）
    for kind in rules.entity_types:
        width, height = kind.size
        add_entity_def(
            scene,
            kind.name,
            color=kind.color,
            width=width,
            height=height,
            field_types=dict(kind.fields),
        )

    project["levels"].clear()  # 範本附的 Level_0
    grid = project["defaultGridSize"]
    levels: dict[str, Level] = {}
    for name, (x, y, w, h) in placed.levels.items():  # 世界座標以格為單位（D-009）
        levels[name] = add_level(
            scene, name, width_cells=w, height_cells=h, world_x=x * grid, world_y=y * grid
        )

    value = {k.name: k.value for k in rules.kinds}
    kind_of = {e.name: e.kind for e in (*rules.areas, *rules.paths)}
    for element, cells in placed.cells.items():
        if kind_of[element] not in value:
            raise SceneError(f"{element}.kind：[kinds] 裡沒有 {kind_of[element]}")
        level = placed.level_of[element]
        paint_cells(scene, level, cells=sorted(cells), value=value[kind_of[element]])
    entities_def = next(d["uid"] for d in project["defs"]["layers"] if d["type"] == "Entities")
    entity_layers = {
        name: next(ly for ly in lv["layerInstances"] or [] if ly["layerDefUid"] == entities_def)
        for name, lv in levels.items()
    }
    for entity in rules.entities:
        level = levels[placed.level_of[entity.name]]
        entity_layer = entity_layers[level["identifier"]]
        x, y = placed.entities[entity.name]
        before = len(entity_layer["entityInstances"])
        try:
            instance = place_entity(scene, level["identifier"], entity.type, x=x, y=y)
        except SceneError as error:
            # 位置由配置器保證在 level 內、圖層上面檢查過；剩下會失敗的都出在 entity 定義
            raise SceneError(f"{entity.name}.type：{error}") from error
        if len(entity_layer["entityInstances"]) != before + 1:
            # 到了 maxCount，先前擺的 entity 被挪走或丟掉
            raise SceneError(f"{entity.name}.type：{entity.type} 的數量到上限了")
        instance["iid"] = element_iid(rules.seed, entity.name)

    project["iid"] = derived_iid(rules.seed, "project")
    project["dummyWorldIid"] = derived_iid(rules.seed, "world")
    defs = {d["uid"]: d["identifier"] for d in project["defs"]["layers"]}
    for name, level in levels.items():
        level["iid"] = derived_iid(rules.seed, f"level:{name}")
        for layer in level["layerInstances"] or []:
            key = f"layer:{name}:{defs[layer['layerDefUid']]}"
            layer["iid"] = derived_iid(rules.seed, key)
            layer["seed"] = random.Random(f"{rules.seed}:{key}").randrange(10**6)
    refresh_derived(project)
    return scene, placed
