"""RULE-1、RULE-2：讀取並檢查規則檔。"""

import copy
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ldtk_map_creator.core.errors import SceneError
from ldtk_map_creator.core.rules import (
    AreaRule,
    EntityRule,
    PathRule,
    Rules,
    load_rules,
    parse_rules,
)
from tests.rule_strategies import rules, to_toml_data

FAST = settings(max_examples=300, deadline=None)
BROKEN = settings(max_examples=1000, deadline=None)
EXAMPLE = Path(__file__).parent / "data" / "village.rules.toml"
MISSING = "zz_missing_ref"  # 比策略產生的名稱長，不會撞名
BAD_VALUES: list[object] = [True, 1.5, {}, [], "x?"]


@pytest.mark.law("RULE-1")
@FAST
@given(rules())
def test_valid_rules_round_trip(expected: Rules) -> None:
    assert parse_rules(to_toml_data(expected)) == expected


def _elements(data: dict[str, Any]) -> list[tuple[str, int, dict[str, Any]]]:
    return [
        (key, i, e) for key in ("area", "path", "entity") for i, e in enumerate(data.get(key, []))
    ]


@st.composite
def broken(draw: st.DrawFn) -> tuple[dict[str, Any], str]:
    """合法的規則弄壞一處；回傳弄壞後的資料，與訊息裡該出現的位置。"""
    good = draw(rules())
    data = copy.deepcopy(to_toml_data(good))
    elements = _elements(data)
    kinds: dict[str, dict[str, Any]] = data["kinds"]
    options = ["top_key", "level_key", "level_size", "bad_seed", "kind_value", "kind_color"]
    options.append("bool_int")
    if elements:
        options += ["element_key", "bad_type", "missing_kind_or_ref", "self_ref"]
    if len(elements) >= 2:
        options.append("duplicate")
    if any(a.at is not None for a in good.areas):
        options.append("two_cycle")
    if good.areas:
        options.append("area_size")
    if good.entities:
        options.append("entity_out")
    match draw(st.sampled_from(options)):
        case "top_key":
            data["extra"] = 1
            return data, "規則檔.extra"
        case "level_key":
            data["level"]["extra"] = 1
            return data, "level.extra"
        case "level_size":
            data["level"]["size"][draw(st.integers(0, 1))] = draw(st.integers(-3, 0))
            return data, "level.size"
        case "bad_seed":
            data["seed"] = draw(st.sampled_from(BAD_VALUES))
            return data, "seed"
        case "bool_int":
            # TOML 的 true 在 Python 是 int 的子類別，整數欄位要擋下來
            slots: list[tuple[dict[str, Any] | list[Any], str | int, str]] = [
                (data, "seed", "seed"),
                (data["level"]["size"], 0, "level.size"),
            ]
            slots += [(k, "value", f"kinds.{n}.value") for n, k in kinds.items()]
            for _, _, e in elements:
                for field in ("size", "at"):
                    if isinstance(e.get(field), list):
                        slots.append((e[field], 1, f"{e['name']}.{field}"))
                slots += [(e, f, f"{e['name']}.{f}") for f in ("gap", "width") if f in e]
            box, key, where = draw(st.sampled_from(slots))
            box[key] = True  # type: ignore[index]
            return data, where
        case "kind_value":
            name = draw(st.sampled_from(sorted(kinds)))
            others = [k["value"] for n, k in kinds.items() if n != name]
            kinds[name]["value"] = draw(st.sampled_from([*BAD_VALUES, 0, -1, *others]))
            # 撞到的值會報在排在後面的那個種類
            order = list(kinds)
            clash = [n for n in order if n != name and kinds[n]["value"] == kinds[name]["value"]]
            later = max([name, *clash], key=order.index)
            return data, f"kinds.{later}.value"
        case "kind_color":
            name = draw(st.sampled_from(sorted(kinds)))
            kinds[name]["color"] = draw(st.sampled_from([*BAD_VALUES, "#12345", "red", "#GGGGGG"]))
            return data, f"kinds.{name}.color"
        case "element_key":
            _, _, element = draw(st.sampled_from(elements))
            element["extra"] = 1
            return data, f"{element['name']}.extra"
        case "bad_type":
            key, i, element = draw(st.sampled_from(elements))
            field = draw(st.sampled_from(sorted(element)))
            element[field] = draw(st.sampled_from(BAD_VALUES))
            where = f"{key}[{i}]" if field == "name" else element["name"]
            return data, f"{where}.{field}"
        case "missing_kind_or_ref":
            _, _, element = draw(st.sampled_from(elements))
            fields = [f for f in ("kind", "near", "from", "to") if f in element]
            if isinstance(element.get("at"), str) and element["at"] != "center":
                fields.append("at")
            if not fields:  # entity 擺在固定的格：換成不存在的錨點
                fields = ["at"]
            field = draw(st.sampled_from(fields))
            element[field] = f"{MISSING}.end" if field == "at" else MISSING
            return data, f"{element['name']}.{field}"
        case "self_ref":
            refs = [e for _, _, e in elements if "near" in e or "from" in e]
            if not refs:
                return data | {"extra": 1}, "規則檔.extra"
            element = draw(st.sampled_from(refs))
            field = "near" if "near" in element else "from"
            element[field] = element["name"]
            return data, f"{element['name']}.{field}"
        case "duplicate":
            i, j = sorted(
                draw(
                    st.lists(st.integers(0, len(elements) - 1), min_size=2, max_size=2, unique=True)
                )
            )
            elements[j][2]["name"] = elements[i][2]["name"]
            return data, f"{elements[i][2]['name']}.name"
        case "two_cycle":
            # x 原本固定位置，改成靠近一個（直接或間接）指向 x 的元素 y，形成 x → y → x
            fixed = [e for e in data["area"] if "at" in e]
            x = draw(st.sampled_from(fixed))
            pointing = [
                (e, f)
                for _, _, e in elements
                for f in ("near", "from", "to")
                if e.get(f) == x["name"]
            ]
            if not pointing:
                x["near"] = x["name"]
                del x["at"]
                return data, f"{x['name']}.near"
            y, _ = draw(st.sampled_from(pointing))
            x["near"] = y["name"]
            del x["at"]
            # 其他元素都只指向排在前面的，所以環一定經過 x，由某條指向 x 的邊報出
            return data, "|".join(f"{e['name']}.{f}" for e, f in pointing)
        case "area_size":
            area = draw(st.sampled_from(data["area"]))
            w, h = data["level"]["size"]
            area["size"] = draw(st.sampled_from([[0, 1], [1, -2], [w + 1, 1], [1, h + 1]]))
            return data, f"{area['name']}.size"
        case _:  # entity_out
            entity = draw(st.sampled_from(data["entity"]))
            w, h = data["level"]["size"]
            entity["at"] = draw(st.sampled_from([[w, 0], [0, h], [-1, 0]]))
            return data, f"{entity['name']}.at"


@pytest.mark.law("RULE-2")
@BROKEN
@given(broken())
def test_broken_rules_are_rejected(case: tuple[dict[str, Any], str]) -> None:
    data, wheres = case
    with pytest.raises(SceneError) as error:
        parse_rules(data)
    message = str(error.value)
    assert any(message.startswith(f"{w}：") for w in wheres.split("|")), (wheres, message)


def test_example_village() -> None:
    """典型的規則檔：每種元素與錨點各用一次，照檔案順序讀出。"""
    r = load_rules(EXAMPLE)
    assert (r.seed, r.level_name, r.level_size) == (7, "Village", (40, 30))
    assert [k.name for k in r.kinds] == ["plaza", "house", "road"]
    assert r.areas[0] == AreaRule("plaza", "plaza", (8, 8), at="center")
    assert r.areas[2] == AreaRule("house_2", "house", (6, 5), near="plaza", side="east", gap=3)
    assert r.paths == (PathRule("main_road", "road", "plaza", "edge:south", 2),)
    assert r.entities == (EntityRule("spawn", "Spawn", "main_road.end"),)


def test_toml_syntax_error(tmp_path: Path) -> None:
    """TOML 本身寫錯也是 SceneError，訊息帶檔名。"""
    path = tmp_path / "bad.rules.toml"
    path.write_text("seed = \n", encoding="utf-8")
    with pytest.raises(SceneError, match=r"bad.rules.toml"):
        load_rules(path)
