"""範本（glossary：範本）：LDtk 1.5.3 存的空專案，新的物件從它複製（D-006）。"""

import copy
import json
from functools import cache
from importlib import resources
from typing import cast

from . import convention
from .errors import SceneError
from .model import EntityDef, EntityInstance, FieldDef, LayerInstance, Level, Project


@cache
def _cached() -> Project:
    text = resources.files(__package__).joinpath("templates", "single.ldtk").read_text("utf-8")
    return cast(Project, json.loads(text))


@cache
def _fields() -> Project:
    """LDtk 1.5.3 存的欄位範本：一個 Portal 定義，帶 Int、Float、Bool、String、EntityRef 欄位。"""
    text = resources.files(__package__).joinpath("templates", "fields.ldtk").read_text("utf-8")
    return cast(Project, json.loads(text))


def field_def(field_type: str) -> FieldDef:
    """某個型別的欄位定義的複本；identifier 與 uid 由呼叫端填。"""
    for edef in _fields()["defs"]["entities"]:
        for fdef in edef["fieldDefs"]:
            if fdef["__type"] == field_type:
                return copy.deepcopy(fdef)
    raise SceneError(f"欄位範本裡沒有 {field_type} 型別")


def entity_def() -> EntityDef:
    """範本 entity 定義（PlayerSpawn）的複本，不帶欄位與 tags。

    identifier、uid、顏色、大小由呼叫端填。
    """
    base = next(
        (d for d in _cached()["defs"]["entities"] if d["identifier"] == convention.PLAYER_SPAWN),
        None,
    )
    if base is None:
        raise SceneError(f"範本裡沒有 {convention.PLAYER_SPAWN} 可以當 entity 定義的底")
    found = copy.deepcopy(base)
    found["fieldDefs"] = []
    found["tags"] = []  # PlayerSpawn 帶 player tag；宣告的類型不是出生點
    return found


def entity_identifiers() -> list[str]:
    """範本裡 entity 定義的名稱（唯讀，不複製整份範本）。"""
    return [d["identifier"] for d in _cached()["defs"]["entities"]]


def project() -> Project:
    """範本專案的一份新複本。"""
    return copy.deepcopy(_cached())


def level() -> Level:
    """範本 level 的一份新複本。"""
    return copy.deepcopy(_cached()["levels"][0])


def layer(layer_type: str) -> LayerInstance:
    """新圖層的底。

    LDtk 各型別的圖層實例欄位相同；範本沒有的型別借用 Entities 的，內容在呼叫端清空。
    """
    layers = {lv["__type"]: lv for lv in _cached()["levels"][0]["layerInstances"] or []}
    base = layers.get(layer_type) or layers.get("Entities")
    if base is None:
        raise SceneError(f"範本沒有 {layer_type} 型別的圖層可以複製")
    return copy.deepcopy(base)


def entity() -> EntityInstance:
    """範本裡的一個 entity 實例的複本。"""
    for found in _cached()["levels"][0]["layerInstances"] or []:
        if found["entityInstances"]:
            return copy.deepcopy(found["entityInstances"][0])
    raise SceneError("範本裡沒有 entity 可以複製")


def entity_field_types(identifier: str) -> dict[str, str] | None:
    """範本裡某個 entity 定義的欄位（名稱 → 型別），照定義的順序；沒有這個定義回 None。

    唯讀，不複製整份範本；規則檔的 `fields` 用它檢查範本三種 entity 的欄位值（D-011）。
    """
    for edef in _cached()["defs"]["entities"]:
        if edef["identifier"] == identifier:
            return {f["identifier"]: f["__type"] for f in edef["fieldDefs"]}
    return None
