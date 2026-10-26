"""真 DAS 行形进入洞察终稿，缺 summary 不用字面 None。"""
from __future__ import annotations

from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

from agent_os_kernel.skills.insight_report import _build_drill, _build_followup, _build_report
from agent_os_kernel.skills.money_fmt import format_gmv
from agent_os_kernel.skills.return_risk_brief import _build_report as _build_return_report
from agent_os_kernel.skills.stat_rows import (
    dash,
    first_present,
    group_by_name,
    metric_names,
    row_dimension,
    row_measure,
    stat_call_matches,
    stat_dicts,
)

scenarios(str(Path(__file__).parent / "features" / "insight_real_das_rows.feature"))


@pytest.fixture
def box():
    return {}

_BINDINGS = {
    "dataset": "ecommerce_sales_v2",
    "time_window": {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"},
}


def _tool(name: str, arguments: dict, data: dict, fingerprint: str) -> dict:
    return {
        "name": name,
        "arguments": arguments,
        "data": data,
        "evidence": {"fingerprint": fingerprint},
    }


def _describe() -> dict:
    return _tool(
        "describe_dataset",
        {"name": "ecommerce_sales_v2"},
        {"row_count": 100000, "name": "ecommerce_sales_v2"},
        "fp-desc",
    )


def _metric_spec(metric: str, *, alias: str | None = None) -> dict:
    if metric == "return_rate":
        return {"function": "avg", "field": "returned", "alias": alias or "avg_returned"}
    return {"function": "sum", "field": "gmv", "alias": alias or "sum_gmv"}


def _stats(group_by: str, metric: str, data: dict, fingerprint: str, *, live: bool) -> dict:
    if not live:
        arguments = {"group_by": group_by, "metric": metric, "name": "ecommerce_sales_v2"}
    elif group_by == "category":
        arguments = {"group_by": [group_by], "metrics": ["sum_gmv"], "dataset_id": "ecommerce_sales_v2"}
    elif group_by == "province":
        arguments = {
            "group_by": [{"field": "province"}],
            "metrics": [_metric_spec("sum_gmv")],
            "dataset_id": "ecommerce_sales_v2",
        }
    else:
        arguments = {
            "group_by": [group_by],
            "metrics": [_metric_spec(metric)],
            "dataset_id": "ecommerce_sales_v2",
        }
    return _tool("query_stats", arguments, data, fingerprint)


def _rows(*, live: bool) -> list[dict]:
    return [
        _describe(),
        _stats(
            "category",
            "sum_gmv",
            {
                "rows": [
                    {"category": "家居生活", "sum_gmv": "934.0200000000"},
                    {"category": "电子产品", "sum_gmv": 1200.5},
                ]
            },
            "fp-cat",
            live=live,
        ),
        _stats(
            "channel",
            "sum_gmv",
            {"groups": [{"channel": "京东", "sum_gmv": 110}, {"key": "天猫", "value": 80}]},
            "fp-ch",
            live=live,
        ),
        _stats(
            "province",
            "sum_gmv",
            {"result": {"rows": [{"dimensions": {"province": "上海"}, "metrics": {"sum_gmv": 50.5}}]}},
            "fp-prov",
            live=live,
        ),
        _stats(
            "channel",
            "return_rate",
            {
                "items": [
                    {"channel": "拼多多", "avg_returned": 12.34},
                    {"channel": "抖音电商", "return_rate": 4},
                ]
            },
            "fp-ret",
            live=live,
        ),
    ]


def _assert_numbers(text: str) -> None:
    assert "100000" in text
    assert "家居生活**（934.02）" in text
    assert "电子产品=1200.5" in text
    assert "京东**（110）" in text
    assert "天猫=80" in text
    assert "上海**（50.5）" in text
    assert "拼多多**（12.34%）" in text
    assert "934.0200000000" not in text


def _assert_fingerprints(text: str) -> None:
    assert text.index("fp-cat") < text.index("fp-ch") < text.index("fp-prov") < text.index("fp-ret")
    assert text.count("fp-cat") == 1
    assert text.count("fp-ch") == 1


def _assert_no_none(text: str) -> None:
    assert "None" not in text
    assert format_gmv(None) == "—"


@given("洞察工具结果用 stub 参数但行是真 DAS 的类目渠道省退货聚合且没有 summary")
def given_stub_args(box):
    box["results"] = _rows(live=False)
    box["bindings"] = dict(_BINDINGS)


@given("洞察工具结果用 live 的 group_by 与 metrics 且行是真 DAS 聚合且没有 summary")
def given_live_args(box):
    box["results"] = _rows(live=True)
    box["bindings"] = dict(_BINDINGS)


@given("渠道追问的最后一条是真 DAS 行京东与 sum_gmv")
def given_followup(box):
    box["followup"] = [
        _tool(
            "query_stats",
            {"group_by": ["channel"], "metrics": ["sum_gmv"]},
            {"result": {"rows": [{"channel": "京东", "sum_gmv": 880.5}, {"channel": "天猫", "sum_gmv": 10}]}},
            "fp-follow",
        )
    ]


@given("退货聚合行是渠道拼多多的 avg_returned 而不是 key value")
def given_return(box):
    def stats(group_by: str, rows: list[dict], fingerprint: str) -> dict:
        return _tool(
            "query_stats",
            {
                "group_by": [group_by],
                "metrics": [{"function": "avg", "field": "returned", "alias": "avg_returned"}],
            },
            {"rows": rows},
            fingerprint,
        )

    box["return_results"] = [
        stats("channel", [{"channel": "拼多多", "avg_returned": 0.13, "sum_returned": 26, "count": 200}], "fp-pdd"),
        stats("category", [{"category": "服饰鞋包", "metrics": {"avg_returned": 0.09}}], "fp-cat-r"),
        stats(
            "province",
            [{"dimensions": {"province": "广东"}, "values": {"avg_returned": 0.11}}],
            "fp-gd",
        ),
    ]
    box["return_bindings"] = {"time_window": {"label": "近7天", "start": "2025-10-15", "end": "2025-10-22"}}


@when("用这些结果组装洞察终稿")
def when_report(box):
    box["text"] = _build_report(box["results"], box["bindings"])


@when("组装这条渠道追问")
def when_followup(box):
    box["text"] = _build_followup("channel", box["followup"])


@when("用这些结果组装第2条依据")
def when_drill(box):
    box["text"] = _build_drill(box["results"], "第2条依据是什么")


@when("组装退货风险简报")
def when_return(box):
    box["text"] = _build_return_report(box["return_results"], box["return_bindings"])


@then("终稿写出家居生活、京东、上海、拼多多和对应数字")
def then_numbers(box):
    _assert_numbers(box["text"])


@then("终稿的证据指纹分别对上四次聚合")
def then_fps(box):
    _assert_fingerprints(box["text"])


@then("终稿不出现字面 None")
def then_no_none(box):
    _assert_no_none(box["text"])


@then("缺失的日期、总 GMV 和退货率显示为破折号")
def then_dashes(box):
    text = box["text"]
    assert "日期 — ~ —" in text
    assert "总 GMV ≈ —" in text
    assert "整体退货率 —%" in text


@then("追问正文写出京东和 880.5 且不出现字面 None")
def then_followup(box):
    text = box["text"]
    assert "京东" in text
    assert "880.5" in text
    assert "天猫=10" in text
    assert "None" not in text


@then("依据正文写出渠道京东且不出现字面 None")
def then_drill(box):
    text = box["text"]
    assert "渠道 **京东**" in text
    assert "None" not in text


@then("简报写出拼多多和退货率 13% 且不出现字面 None")
def then_return(box):
    text = box["text"]
    assert "拼多多" in text
    assert "退货率 13%" in text
    assert "广东" in text
    assert "服饰鞋包" in text
    assert "None" not in text


def test_stat_row_edges_do_not_invent_none():
    assert stat_dicts(None) == []
    assert stat_dicts({"result": {"n": 1}}) == []
    assert stat_dicts({"rows": ["skip", {"category": "家居生活"}]}) == [{"category": "家居生活"}]
    assert row_dimension("nope") is None
    assert row_dimension({"dimensions": "bad", "group": {"channel": "京东"}}, "province") == "京东"
    assert row_dimension({"channel": "京东"}, "sku") == "京东"
    assert row_dimension({}) is None
    assert row_measure("nope", "sum_gmv") is None
    assert row_measure({}, "sum_gmv") is None
    assert row_measure({"return_rate": 4}, "return_rate") == 4
    assert row_measure({"values": {"value": 3}}, "sum_gmv") == 3
    assert group_by_name(None) == ""
    assert group_by_name([]) == ""
    assert group_by_name([{"alias": "  "}]) == ""
    assert metric_names(None) == set()
    assert metric_names({"metric": 1, "metrics": {"alias": "sum_gmv"}}) == {"sum_gmv"}
    assert stat_call_matches({"group_by": ["channel"], "metrics": [{"alias": "avg_returned"}]}, "channel", "return_rate")
    assert not stat_call_matches({"group_by": ["province"]}, "channel", "sum_gmv")
    assert dash(None) == "—"
    assert dash(0) == "0"
    assert first_present("", None) == "—"
    assert format_gmv(None) == "—"
