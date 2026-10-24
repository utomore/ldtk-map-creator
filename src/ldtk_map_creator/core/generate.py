"""照規則生成場景地圖（D-007、D-009、D-010、D-011）：配置之後畫進每個 level。

同規則同 seed，寫出的檔完全相同：iid 與圖層的 seed 都由 seed 加名字推出。
"""

import random
from pathlib import Path

from . import convention, fields
from .derived import refresh_derived
from .edit import add_entity_def, paint_cells, place_entity
from .errors import SceneError
from .ids import derived_iid
from .layout import Layout, layout
from .model import EntityInstance, IntGridValueDef, Level
from .rules import EntityRule, Kind, LayerValue, Rules
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
    layer_defs = {d["identifier"]: d for d in project["defs"]["layers"]}
    for name in convention.LAYERS:
        found = [d["type"] for d in project["defs"]["layers"] if d["identifier"] == name]
        wanted = "Entities" if name == convention.ENTITIES else "IntGrid"
        if found != [wanted]:
            raise SceneError(f"範本要剛好一個 {wanted} 圖層叫 {name}，現在是 {found}（D-011）")
    for name, values in _layer_values(rules).items():
        layer_defs[name]["intGridValues"] = values
    # 規則宣告的 entity 類型變成 LDtk 的 entity 定義，加在範本的三種 entity 旁邊（D-010、D-011）
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

    kinds = {k.name: k for k in rules.kinds}
    grounds = {v.name: v.value for v in rules.grounds}
    collisions = {v.name: v.value for v in rules.collisions}
    kind_of = {e.name: e.kind for e in (*rules.areas, *rules.paths)}
    for element, cells in placed.cells.items():
        kind = kinds.get(kind_of[element])
        if kind is None:
            raise SceneError(f"{element}.kind：[kinds] 裡沒有 {kind_of[element]}")
        level = placed.level_of[element]
        for layer, value in _kind_values(kind, grounds, collisions):
            paint_cells(scene, level, cells=sorted(cells), value=value, layer=layer)
    for entity in rules.entities:
        x, y = placed.entities[entity.name]
        iid = element_iid(rules.seed, entity.name)
        _place_rule_entity(scene, placed.level_of[entity.name], entity, (x, y), iid)

    # 傳送點：兩端各擺一個 Teleport，用 targetPort 互指（D-010、D-011）
    for portal in rules.portals:
        (from_level, from_cell), (to_level, to_cell) = placed.portals[portal.name]
        start, end = portal_iids(rules.seed, portal.name)
        teleport = convention.TELEPORT
        first = _place(scene, from_level, teleport, from_cell, start, f"{portal.name}.from")
        # 第二端擺的時候第一端已經在了，直接帶上指向它的值
        refs: dict[str, object] = {convention.TELEPORT_TARGET: start}
        _place(scene, to_level, teleport, to_cell, end, f"{portal.name}.to", refs)
        # 第一端的引用直接寫進欄位；__value 由最後的 refresh_derived 補
        edef = next(d for d in project["defs"]["entities"] if d["identifier"] == teleport)
        fdef = next(f for f in edef["fieldDefs"] if f["identifier"] == convention.TELEPORT_TARGET)
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


def _layer_values(rules: Rules) -> dict[str, list[IntGridValueDef]]:
    """四個 IntGrid 層的值定義（D-011）：Ground、Collision 照規則檔；Height 到用到的最大階數。"""

    def define(value: int, identifier: str, color: str) -> IntGridValueDef:
        return {
            "value": value,
            "identifier": identifier,
            "color": color,
            "tile": None,
            "groupUid": 0,
        }

    def declared(values: tuple[LayerValue, ...]) -> list[IntGridValueDef]:
        return [define(v.value, v.name, v.color) for v in values]

    top = max((k.height for k in rules.kinds), default=0)
    return {
        convention.GROUND: declared(rules.grounds),
        convention.COLLISION: declared(rules.collisions),
        convention.HEIGHT: [
            define(n, convention.height_identifier(n), convention.height_color(n))
            for n in range(1, top + 1)
        ],
        convention.SLOPE: [define(1, convention.SLOPE_VALUE, convention.SLOPE_COLOR)],
    }


def _kind_values(
    kind: Kind, grounds: dict[str, int], collisions: dict[str, int]
) -> list[tuple[str, int]]:
    """圖格種類要畫的（層名, 值）；沒寫的層不畫。"""
    values: list[tuple[str, int]] = []
    if kind.ground is not None:
        values.append((convention.GROUND, grounds[kind.ground]))
    if kind.collision is not None:
        values.append((convention.COLLISION, collisions[kind.collision]))
    if kind.height > 0:
        values.append((convention.HEIGHT, kind.height))
    if kind.slope:
        values.append((convention.SLOPE, 1))
    return values


def _entity_count(scene: SceneMap) -> int:
    return sum(
        len(ly["entityInstances"])
        for lv in scene.project["levels"]
        for ly in lv["layerInstances"] or []
    )


def _place_rule_entity(
    scene: SceneMap, level: str, entity: EntityRule, cell: tuple[int, int], iid: str
) -> None:
    """擺規則檔的 `[[entity]]`，帶上它寫的欄位值；錯誤開頭是 `<元素>.type`。

    欄位的名稱與型別讀規則檔時就查過（RULE-2），生成的定義沒有範圍限制，
    所以擺不下的原因出在類型本身（不存在、數量上限）。
    """
    _place(scene, level, entity.type, cell, iid, f"{entity.name}.type", dict(entity.fields))


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
