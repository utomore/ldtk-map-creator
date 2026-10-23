"""操作（glossary：操作）：入口直接呼叫的單位，一個操作對應一個 MCP 工具與一個 CLI 指令。

每個會改動地圖的操作都是一個小交易（SCENE-5）：
1. 從磁碟讀場景地圖，在記憶體裡做完那件事。
2. 找出內容有變的 level，先在記憶體裡 render 好它們的預覽圖。
3. 寫回場景地圖（只寫有變的 level）與預覽圖；中途失敗就把動過的檔還原。
所以 MCP 與 CLI 不用保存狀態，兩邊的行為也一樣（P-003）。
"""

import contextlib
import copy
import functools
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from . import edit, record, scene
from . import generate as generation
from . import render as rendering
from .derived import cell_count, refresh_derived
from .errors import SceneError
from .files import atomic_write
from .rules import load_rules
from .scene import SceneMap

ACTIONS = (
    "create_scene",
    "describe_scene",
    "add_level",
    "paint_rect",
    "paint_cells",
    "place_entity",
    "move_entity",
    "delete_entity",
    "render_level",
    "generate_scene",
    "add_entity_def",
    "set_entity_fields",
)
"""所有操作的名稱；MCP 工具與 CLI 指令跟它一一對應（ENTRY-1）。"""


@dataclass(frozen=True)
class ActionResult:
    """操作的結果：給人看的摘要、這次更新的預覽圖，與新擺的 entity 的 iid（有的話）。"""

    summary: str
    previews: tuple[Path, ...] = ()
    iid: str | None = None


# 操作是「讀、改、寫」的交易；MCP 會在多個執行緒同時跑工具，所以全部排隊執行
_LOCK = threading.RLock()


def _serialized[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(fn)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        with _LOCK:
            return fn(*args, **kwargs)

    return run


def _snapshot(paths: set[Path]) -> dict[Path, bytes | None]:
    return {p: p.read_bytes() if p.is_file() else None for p in paths}


def _restore(saved: dict[Path, bytes | None]) -> None:
    """還原真的被改過的檔；某個檔還原失敗也繼續還原其餘的。"""
    for path, data in saved.items():
        with contextlib.suppress(OSError, SceneError):
            current = path.read_bytes() if path.is_file() else None
            if current == data:
                continue
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, data)


def _for_render(level: object) -> object:
    """比較預覽圖會不會變時，不看跟畫面無關的相鄰關係。"""
    if isinstance(level, dict):
        return {k: v for k, v in cast(dict[str, object], level).items() if k != "__neighbours"}
    return level


def _commit(
    target: SceneMap,
    before: dict[str, object] | None,
    summary: str,
    primary: str,
    iid: str | None = None,
    extra: dict[Path, bytes] | None = None,
    remove: set[Path] | None = None,
    generated: dict[str, Any] | None = None,
) -> ActionResult:
    """寫回有變的 level 與它們的預覽圖；失敗時還原磁碟上動過的檔。

    - `before` 是操作前每個 level（以 iid 為鍵）的複本；None 表示全部都是新的。
    - `primary` 是這次操作的目標 level：就算內容沒變，也一定回傳它的預覽圖。
    - `extra` 是同一個交易裡要一起寫的其他檔（例如生成紀錄）；`remove` 是要一起刪的檔。
    - `generated` 是預覽圖要用的生成紀錄（寫上元素名稱）；None 就讀磁碟上的。
    """
    extra = extra or {}
    remove = remove or set()
    refresh_derived(target.project)
    levels = target.project["levels"]
    changed = [lv["identifier"] for lv in levels if before is None or before.get(lv["iid"]) != lv]
    redraw = [
        lv["identifier"]
        for lv in levels
        if before is None
        or _for_render(before.get(lv["iid"])) != _for_render(lv)
        or lv["identifier"] == primary
    ]
    labels_by_level, note = _names(target, redraw, generated, refreshed=True)
    summary += note
    images = {
        level_id: rendering.render_level(target, level_id, labels=labels_by_level.get(level_id, []))
        for level_id in redraw
    }
    folder = target.path.parent / target.path.stem
    names = {lv["identifier"] for lv in levels}
    stale = {p for p in folder.glob("*.ldtkl") if p.stem not in names}
    touched = {target.path, *stale, *(folder / f"{lv}.ldtkl" for lv in changed)}
    touched |= {rendering.preview_path(target, lv) for lv in redraw} | extra.keys() | remove
    saved = _snapshot(touched)
    try:
        scene.save_scene(target, levels=set(changed), refreshed=True)
        previews = tuple(rendering.write_preview(target, lv, image) for lv, image in images.items())
        for path, data in extra.items():
            atomic_write(path, data)
        for old in remove:
            old.unlink(missing_ok=True)
    except BaseException:
        _restore(saved)
        raise
    return ActionResult(summary, previews, iid)


def _apply(path: Path, change: Callable[[SceneMap], tuple[str, str, str | None]]) -> ActionResult:
    """讀場景地圖、做 `change`（回傳摘要、目標 level、新 iid），再交給 _commit 寫回。"""
    target = scene.load_scene(path)
    before: dict[str, object] = {lv["iid"]: copy.deepcopy(lv) for lv in target.project["levels"]}
    summary, primary, iid = change(target)
    return _commit(target, before, summary, primary, iid)


@_serialized
def create_scene(path: Path, *, split: bool = True) -> ActionResult:
    """建一個新的場景地圖，含一個空的 Level_0。"""
    target = scene.new_scene(path, split=split)
    return _commit(
        target, None, f"建好場景地圖 {path}（{'拆開存' if split else '單檔'}）", "Level_0"
    )


@_serialized
def describe_scene(path: Path) -> ActionResult:
    """列出場景地圖的 level、圖層、IntGrid 值、entity 定義與 entity；不改動檔案。"""
    target = scene.load_scene(path)
    refresh_derived(target.project)  # 只在記憶體裡重算，位置不靠檔案裡可能過期的 __grid
    return ActionResult(_describe(target))


def _describe(target: SceneMap) -> str:
    project = target.project
    grid = project["defaultGridSize"]
    lines = [f"場景地圖 {target.path}（格子 {grid}px，{'拆開存' if target.split else '單檔'}）"]
    lines.append("圖層：")
    for ldef in project["defs"]["layers"]:
        values = "、".join(
            f"{v['value']}={v['identifier'] or '（未命名）'}（{v['color']}）"
            for v in ldef["intGridValues"]
        )
        extra = f"，值：{values}" if values else ""
        lines.append(f"  - {ldef['identifier']}（{ldef['type']}，{ldef['gridSize']}px{extra}）")
    lines.append("entity 定義：")
    for edef in project["defs"]["entities"]:
        lines.append(f"  - {edef['identifier']}（{edef['width']}x{edef['height']}px）")
    lines.append("level：")
    for level in project["levels"]:
        cols = cell_count(level["pxWid"], grid)
        rows = cell_count(level["pxHei"], grid)
        lines.append(
            f"  - {level['identifier']}：{cols}x{rows} 格，世界座標 "
            f"({level['worldX']}, {level['worldY']})"
        )
        for layer in level["layerInstances"] or []:
            for entity in layer["entityInstances"]:
                gx, gy = entity["__grid"]
                lines.append(f"      {entity['__identifier']} 在 ({gx}, {gy})，iid {entity['iid']}")
    return "\n".join(lines)


@_serialized
def add_level(
    path: Path,
    identifier: str,
    *,
    width_cells: int,
    height_cells: int,
    world_x: int = 0,
    world_y: int = 0,
) -> ActionResult:
    """新增一個空的 level。"""

    def change(target: SceneMap) -> tuple[str, str, str | None]:
        scene.add_level(
            target,
            identifier,
            width_cells=width_cells,
            height_cells=height_cells,
            world_x=world_x,
            world_y=world_y,
        )
        return f"新增 level {identifier}：{width_cells}x{height_cells} 格", identifier, None

    return _apply(path, change)


@_serialized
def paint_rect(
    path: Path,
    level: str,
    *,
    x: int,
    y: int,
    width: int = 1,
    height: int = 1,
    value: int,
    layer: str | None = None,
) -> ActionResult:
    """把矩形範圍內的格子設成 value（0 是清除）。"""

    def change(target: SceneMap) -> tuple[str, str, str | None]:
        changed = edit.paint_rect(
            target, level, x=x, y=y, width=width, height=height, value=value, layer=layer
        )
        return f"{level}：({x},{y}) {width}x{height} 設成 {value}，改了 {changed} 格", level, None

    return _apply(path, change)


@_serialized
def paint_cells(
    path: Path,
    level: str,
    *,
    cells: list[tuple[int, int]],
    value: int,
    layer: str | None = None,
) -> ActionResult:
    """把指定的幾格設成 value（0 是清除）。"""

    def change(target: SceneMap) -> tuple[str, str, str | None]:
        changed = edit.paint_cells(target, level, cells=cells, value=value, layer=layer)
        return f"{level}：{len(cells)} 格設成 {value}，改了 {changed} 格", level, None

    return _apply(path, change)


@_serialized
def place_entity(
    path: Path,
    level: str,
    entity: str,
    *,
    x: int,
    y: int,
    layer: str | None = None,
    values: dict[str, object] | None = None,
) -> ActionResult:
    """在 (x, y) 格擺一個 entity；`values` 是欄位值（欄位名稱 → 值）。"""

    def change(target: SceneMap) -> tuple[str, str, str | None]:
        placed = edit.place_entity(target, level, entity, x=x, y=y, layer=layer, values=values)
        return f"{level}：{entity} 擺在 ({x},{y})，iid {placed['iid']}", level, placed["iid"]

    return _apply(path, change)


@_serialized
def move_entity(path: Path, iid: str, *, x: int, y: int) -> ActionResult:
    """把 entity 移到同一個 level 的 (x, y) 格。"""

    def change(target: SceneMap) -> tuple[str, str, str | None]:
        level, moved = edit.move_entity(target, iid, x=x, y=y)
        name = level["identifier"]
        return f"{name}：{moved['__identifier']} 移到 ({x},{y})", name, None

    return _apply(path, change)


@_serialized
def delete_entity(path: Path, iid: str) -> ActionResult:
    """刪掉一個 entity。"""

    def change(target: SceneMap) -> tuple[str, str, str | None]:
        name = edit.delete_entity(target, iid)["identifier"]
        return f"{name}：刪掉 entity {iid}", name, None

    return _apply(path, change)


@_serialized
def add_entity_def(
    path: Path,
    identifier: str,
    *,
    color: str = "#BE4A2F",
    width: int = 16,
    height: int = 16,
    field_types: dict[str, str] | None = None,
) -> ActionResult:
    """新增一個 entity 定義。

    `field_types` 是欄位名稱 → 型別（Int、Float、Bool、String、EntityRef）。
    """

    def change(target: SceneMap) -> tuple[str, str, str | None]:
        edit.add_entity_def(
            target, identifier, color=color, width=width, height=height, field_types=field_types
        )
        levels = target.project["levels"]
        if not levels:
            raise SceneError("場景地圖還沒有 level，沒有地方出預覽圖；先新增一個 level")
        listed = "、".join(f"{n}（{k}）" for n, k in (field_types or {}).items()) or "沒有欄位"
        return f"新增 entity 定義 {identifier}：{listed}", levels[0]["identifier"], None

    return _apply(path, change)


@_serialized
def set_entity_fields(path: Path, iid: str, values: dict[str, object]) -> ActionResult:
    """設定 entity 的欄位值（欄位名稱 → 值；null 是清空）。"""

    def change(target: SceneMap) -> tuple[str, str, str | None]:
        entity = edit.set_entity_fields(target, iid, values)
        level, _, _ = edit.find_entity(target, iid)
        listed = "、".join(f"{k} = {v!r}" for k, v in values.items())
        return f"{entity['__identifier']}（{iid}）：{listed}", level["identifier"], None

    return _apply(path, change)


@_serialized
def render_level(path: Path, level: str, *, scale: int = 2) -> ActionResult:
    """重新 render 一個 level 的預覽圖，不改動場景地圖。"""
    target = scene.load_scene(path)
    names, note = _names(target, [level], None)
    image = rendering.render_level(target, level, scale=scale, labels=names.get(level, []))
    preview = rendering.write_preview(target, level, image)
    return ActionResult(f"{level} 的預覽圖{note}", (preview,))


@_serialized
def generate_scene(path: Path, *, force: bool = False) -> ActionResult:
    """照 `<名>.rules.toml` 生成場景地圖（D-007、D-008）。

    已經有這個場景地圖時是重新生成：先跟上次生成的紀錄比對，被直接改過就拒絕並列出差異；
    `force=True` 才蓋掉。存法沿用既有的場景地圖，新建時拆開存。
    """
    rules = load_rules(record.rules_path(path))
    # 地圖不在就沒有東西要保護，也不拿殘留的紀錄來比
    previous = _read_record(path, force) if path.exists() else None
    split = _stored_split(path)
    before: dict[str, object] | None = None
    current = _load_current(path, force) if path.exists() else None
    if current is not None:
        split = current.split
        # iid 由 seed 推出，沒變的 level 跟磁碟上的一樣，就不重寫
        before = {lv["iid"]: copy.deepcopy(lv) for lv in current.project["levels"]}
        if not force:
            if previous is None:
                raise SceneError(
                    f"{path} 沒有上次生成的紀錄，不知道有沒有被直接改過，不覆蓋；"
                    "確定要照規則蓋掉就帶 force"
                )
            diff = record.differences(previous["content"], _content(current))
            if diff:
                listed = "\n".join(f"- {line}" for line in diff)
                raise SceneError(
                    f"{path} 在上次生成之後被直接改過，不覆蓋：\n{listed}\n"
                    "要保留這些改動，就把它們寫進規則檔；確定要蓋掉就帶 force"
                )
    # 地圖不在時照樣檢查子資料夾裡有沒有別的 level 檔；那些不是生成的，force 也不蓋
    target, placed = generation.generate_scene(rules, path, split=split, overwrite=path.exists())
    areas = {a.name for a in rules.areas}
    new_record = record.make_record(target, placed, rules.level_name, areas)
    summary = _generate_summary(rules.level_name, rules.level_size, previous, new_record)
    extra = {record.record_path(path): record.dump(new_record)}
    # 舊地圖有、新地圖沒有的 level（改名或拿掉），預覽圖一起刪；Windows 不分大小寫，比對也不分
    kept = {lv["identifier"].lower() for lv in target.project["levels"]}
    gone = [lv["identifier"] for lv in current.project["levels"]] if current else []
    remove = {rendering.preview_path(target, lv) for lv in gone if lv.lower() not in kept}
    return _commit(
        target, before, summary, rules.level_name, extra=extra, remove=remove, generated=new_record
    )


def _names(
    target: SceneMap,
    levels: list[str],
    generated: dict[str, Any] | None,
    *,
    refreshed: bool = False,
) -> tuple[dict[str, list[rendering.Label]], str]:
    """預覽圖要寫的元素名稱，與要補在摘要後面的說明。

    `generated` 是剛生成的紀錄，直接用。沒給就讀磁碟上的紀錄，而且只在地圖跟紀錄一致時才寫：
    直接改過的地圖，名稱可能已經不在元素上了。讀不了或對不上都不擋操作，只在摘要說一聲。
    """
    if generated is None:
        if not record.record_path(target.path).is_file():
            return {}, ""
        try:
            generated = record.read_record(target.path)
        except SceneError:
            return {}, "\n（生成紀錄讀不了，預覽圖沒寫元素名稱）"
        if generated is None or not any(record.labels(generated, lv) for lv in levels):
            return {}, ""
        try:
            same = record.content(target, refreshed=refreshed) == generated["content"]
        except SceneError:
            same = False
        if not same:
            return {}, (
                "\n（地圖跟上次生成的結果不同，預覽圖不寫元素名稱；"
                "把改動寫進規則檔再重新生成，名稱就會回來）"
            )
    return {lv: record.labels(generated, lv) for lv in levels}, ""


def _content(current: SceneMap) -> record.Content:
    try:
        return record.content(current)
    except SceneError as error:
        raise SceneError(
            f"讀不了 {current.path} 的內容，沒辦法比對：{error}；帶 force 照規則蓋掉"
        ) from error


def _stored_split(path: Path) -> bool:
    """既有的場景地圖怎麼存的（D-002）；讀不了主檔就看子資料夾有沒有 level 檔，新建預設拆開。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(cast(dict[str, Any], data)["externalLevels"])
    except FileNotFoundError:
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return any((path.parent / path.stem).glob("*.ldtkl"))


def _read_record(path: Path, force: bool) -> dict[str, Any] | None:
    """讀生成紀錄；帶 force 時讀不了也沒關係，反正要照規則重寫。"""
    try:
        return record.read_record(path)
    except SceneError:
        if force:
            return None
        raise


def _load_current(path: Path, force: bool) -> SceneMap | None:
    """讀既有的場景地圖；帶 force 時讀不了就當作沒有，照規則重寫。"""
    try:
        return scene.load_scene(path)
    except SceneError:
        if force:
            return None
        raise


def _generate_summary(
    level: str, size: tuple[int, int], previous: dict[str, Any] | None, new: dict[str, Any]
) -> str:
    changed, added, removed = record.moved(previous, new)
    count = len(new["elements"])
    lines = [f"照規則生成 {level}（{size[0]}x{size[1]} 格），{count} 個元素"]
    if previous is None:
        return lines[0]
    if changed:
        lines.append(f"位置變了：{'、'.join(changed)}")
    if added:
        lines.append(f"新的：{'、'.join(added)}")
    if removed:
        lines.append(f"拿掉了：{'、'.join(removed)}")
    if len(lines) == 1:
        lines.append("沒有元素移動")
    return "\n".join(lines)
