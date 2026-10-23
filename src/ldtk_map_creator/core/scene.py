"""場景地圖：從範本建立、新增 level、讀寫檔（D-002、D-006）。"""

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from . import template
from .derived import UNASSIGNED_UID, cell_count, refresh_derived
from .errors import SceneError
from .files import atomic_write
from .model import LayerInstance, Level, Project

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# 拆開存時 level 名稱就是檔名；Windows 不准用這些裝置名稱當檔名
_RESERVED = {"con", "prn", "aux", "nul"} | {f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10)}
_HEADER_KEY = "__header__"


@dataclass
class SceneMap:
    """一個場景地圖：一個 LDtk 專案，裡面有多個 level（glossary：場景地圖）。

    記憶體裡每個 level 的 `layerInstances` 一律是載入好的；
    拆開存時，寫檔才把它搬到 `.ldtkl`。
    """

    path: Path
    project: Project

    @property
    def split(self) -> bool:
        """是否拆開存（`externalLevels`）。"""
        return self.project["externalLevels"]

    def level(self, identifier: str) -> Level:
        for level in self.project["levels"]:
            if level["identifier"] == identifier:
                return level
        raise SceneError(f"找不到 level {identifier}")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _empty_layers(project: Project, level: Level, rng: random.Random) -> list[LayerInstance]:
    layers: list[LayerInstance] = []
    for ldef in project["defs"]["layers"]:
        layer = template.layer(ldef["type"])
        layer["iid"] = ""
        layer["layerDefUid"] = ldef["uid"]
        layer["seed"] = rng.randrange(10**6)
        layer["entityInstances"] = []
        layer["gridTiles"] = []
        layer["autoLayerTiles"] = []
        if ldef["type"] == "IntGrid":
            grid = ldef["gridSize"]
            cells = cell_count(level["pxWid"], grid) * cell_count(level["pxHei"], grid)
            layer["intGridCsv"] = [0] * cells
        else:
            layer["intGridCsv"] = []
        layers.append(layer)
    return layers


def new_scene(path: Path, *, split: bool = True) -> SceneMap:
    """從範本建一個新的場景地圖，含一個空的 `Level_0`；還沒寫檔。"""
    if path.suffix != ".ldtk":
        raise SceneError(f"場景地圖的主檔要是 .ldtk：{path}")
    if path.exists():
        raise SceneError(f"已經有這個檔，不覆蓋：{path}")
    project = template.project()
    project["iid"] = ""
    project["dummyWorldIid"] = ""
    project["externalLevels"] = split
    level = project["levels"][0]
    level["iid"] = ""
    level["externalRelPath"] = None
    level["layerInstances"] = _empty_layers(project, level, random.Random())
    refresh_derived(project)
    return SceneMap(path=path, project=project)


def add_level(
    scene: SceneMap,
    identifier: str,
    *,
    width_cells: int,
    height_cells: int,
    world_x: int = 0,
    world_y: int = 0,
) -> Level:
    """新增一個空的 level；尺寸以格為單位，世界座標以像素為單位。"""
    project = scene.project
    if not _IDENTIFIER.fullmatch(identifier):
        raise SceneError(f"level 名稱只能用英數字與底線，且不以數字開頭：{identifier}")
    # 拆開存時檔名就是 level 名稱；不分大小寫的檔案系統上，大小寫不同也會撞檔
    if any(lv["identifier"].lower() == identifier.lower() for lv in project["levels"]):
        raise SceneError(f"已經有同名的 level（不分大小寫）：{identifier}")
    if identifier.lower() in _RESERVED:
        raise SceneError(f"level 名稱不能用 Windows 的裝置名稱：{identifier}")
    if width_cells < 1 or height_cells < 1:
        raise SceneError(f"level 尺寸至少 1 格：{width_cells}x{height_cells}")
    grid = project["defaultGridSize"]
    level = template.level()
    level["identifier"] = identifier
    level["useAutoIdentifier"] = False
    level["iid"] = ""
    level["uid"] = UNASSIGNED_UID
    level["pxWid"] = width_cells * grid
    level["pxHei"] = height_cells * grid
    level["worldX"] = world_x
    level["worldY"] = world_y
    level["externalRelPath"] = None
    level["layerInstances"] = _empty_layers(project, level, random.Random())
    project["levels"].append(level)
    try:
        refresh_derived(project)
    except SceneError:
        project["levels"].remove(level)
        raise
    return level


def _inside(folder: Path, rel: str) -> Path:
    target = (folder / rel).resolve()
    if not target.is_relative_to(folder.resolve()):
        raise SceneError(f"level 檔的路徑跑出場景地圖的資料夾：{rel}")
    if not target.is_file():
        raise SceneError(f"找不到 level 檔：{rel}")
    return target


def load_scene(path: Path) -> SceneMap:
    """讀一個場景地圖；拆開存的 level 從 `.ldtkl` 載入。讀不了的一律丟 SceneError。"""
    if not path.is_file():
        raise SceneError(f"找不到場景地圖：{path}")
    try:
        return _load(path)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SceneError(f"讀不了場景地圖 {path}：{error!r}") from error


def _load(path: Path) -> SceneMap:
    project = cast(Project, _load_json(path))
    for i, level in enumerate(project["levels"]):
        rel = level["externalRelPath"]
        if rel is None:
            continue
        loaded = cast(dict[str, Any], _load_json(_inside(path.parent, rel)))
        loaded.pop(_HEADER_KEY, None)
        full = cast(Level, loaded)
        full["externalRelPath"] = None
        project["levels"][i] = full
    return SceneMap(path=path, project=project)


def _write_json(path: Path, data: object) -> None:
    text = json.dumps(data, ensure_ascii=False, indent="\t") + "\n"
    atomic_write(path, text.encode("utf-8"))


def save_scene(scene: SceneMap) -> list[Path]:
    """寫出場景地圖，回傳寫了哪些檔。

    主檔寫好之後才刪掉子資料夾裡沒有對應 level 的 `.ldtkl`（單檔時全部刪），
    所以中途失敗時，舊主檔指向的檔都還在。
    """
    project = scene.project
    refresh_derived(project)
    written: list[Path] = []
    main = cast(dict[str, Any], dict(project))
    if scene.split:
        header = main.get(_HEADER_KEY)
        levels: list[dict[str, Any]] = []
        for level in cast(list[dict[str, Any]], project["levels"]):
            rel = f"{scene.path.stem}/{level['identifier']}.ldtkl"
            sub = scene.path.parent / rel
            _write_json(sub, {_HEADER_KEY: header, **level, "externalRelPath": None})
            written.append(sub)
            levels.append({**level, "externalRelPath": rel, "layerInstances": None})
        main["levels"] = levels
    _write_json(scene.path, main)
    # 主檔寫好之後才清：單檔時整個子資料夾的 .ldtkl 都是之前拆開存留下的
    folder = scene.path.parent / scene.path.stem
    kept = {p.resolve() for p in written}
    for stale in folder.glob("*.ldtkl"):
        if stale.resolve() not in kept:
            stale.unlink()
    if folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
    written.append(scene.path)
    return written
