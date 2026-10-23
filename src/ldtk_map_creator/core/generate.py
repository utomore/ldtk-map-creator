"""照規則生成場景地圖（D-007、D-009、D-010）：配置之後畫進每個 level。

同規則同 seed，寫出的檔完全相同：iid 與圖層的 seed 都由 seed 加名字推出。
"""

import random
from pathlib import Path

from . import fields
from .derived import refresh_derived
from .edit import add_entity_def, paint_cells, place_entity
from .errors import SceneError
from .ids import derived_iid
from .layout import Layout, layout
from .model import EntityInstance, Level
from .rules import Rules, portal_ref_field
from .scene import SceneMap, add_level, new_scene


def element_iid(seed: int, name: str) -> str:
    """元素產生的 entity 的 iid；傳送點兩端是 `<名稱>:from`、`<名稱>:to`。"""
    return derived_iid(seed, f"element:{name}")


def portal_iids(seed: int, name: str) -> tuple[str, str]:
    """傳送點兩端 entity 的 iid（from 那端, to 那端）。"""
    return element_iid(seed, f"{name}:from"), element_iid(seed, f"{name}:to")


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
    # 規則宣告的 entity 類型變成 LDtk 的 entity 定義，加在範本的 Spawn 旁邊（D-010）
    for kind in rules.entity_types:
        width, height = kind.size
        try:
            add_entity_def(
                scene,
                kind.name,
                color=kind.color,
                width=width,
                height=height,
                field_types=dict(kind.fields),
            )
        except SceneError as error:
            raise SceneError(f"entity_types.{kind.name}：{error}") from error

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
    for entity in rules.entities:
        x, y = placed.entities[entity.name]
        iid = element_iid(rules.seed, entity.name)
        _place(scene, placed.level_of[entity.name], entity.type, (x, y), iid, f"{entity.name}.type")

    # 傳送點：兩端各擺一個，用類型唯一的 EntityRef 欄位互指（D-010）
    for portal in rules.portals:
        ref = portal_ref_field(rules.entity_types, portal.name, portal.type)  # 先檢查再擺
        (from_level, from_cell), (to_level, to_cell) = placed.portals[portal.name]
        start, end = portal_iids(rules.seed, portal.name)
        first = _place(scene, from_level, portal.type, from_cell, start, f"{portal.name}.from")
        # 第二端擺的時候第一端已經在了，直接帶上指向它的值
        _place(scene, to_level, portal.type, to_cell, end, f"{portal.name}.to", {ref: start})
        # 第一端的引用直接寫進欄位；__value 由最後的 refresh_derived 補
        edef = next(d for d in project["defs"]["entities"] if d["identifier"] == portal.type)
        fdef = next(f for f in edef["fieldDefs"] if f["identifier"] == ref)
        instance = next(f for f in first["fieldInstances"] if f["defUid"] == fdef["uid"])
        fields.set_value(instance, fdef, end)

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


def _entity_count(scene: SceneMap) -> int:
    return sum(
        len(ly["entityInstances"])
        for lv in scene.project["levels"]
        for ly in lv["layerInstances"] or []
    )


def _place(
    scene: SceneMap,
    level: str,
    entity_type: str,
    cell: tuple[int, int],
    iid: str,
    label: str,
    values: dict[str, object] | None = None,
) -> EntityInstance:
    """擺一個生成的 entity 並給它推出的 iid；錯誤訊息開頭是 `label`。

    到了 maxCount 時 LDtk 的規則會挪走或丟掉先前擺的（可能在別的 level），
    所以數整個場景地圖：多了剛好一個才算擺成。
    """
    before = _entity_count(scene)
    x, y = cell
    try:
        instance = place_entity(scene, level, entity_type, x=x, y=y, values=values)
    except SceneError as error:
        # 位置由配置器保證在 level 內、圖層上面檢查過；剩下會失敗的都出在 entity 定義
        raise SceneError(f"{label}：{error}") from error
    if _entity_count(scene) != before + 1:
        raise SceneError(f"{label}：{entity_type} 的數量到上限了")
    instance["iid"] = iid
    return instance
