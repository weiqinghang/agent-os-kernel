"""P0′ 产品行为：进度条带、脱敏、时间窗一行确认，以及 UAT 三件必须改。"""
from __future__ import annotations

from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from agent_os_kernel.skills.insight_report import SKILL, stub_decide

scenarios(str(Path(__file__).parent / "features" / "ui_p0_prime.feature"))

WHITELIST = {"处理中", "思考中", "正在查询数据", "正在整理结论", "等确认"}
PROGRESS_SEQUENCE = ["处理中", "思考中", "正在查询数据", "正在整理结论"]


def _call(world, msg):
    world["snap"] = world["node"].call(msg)
    return world["snap"]


@given("调试关闭的洞察助手")
def open_ui(world):
    snap = world["snap"]
    assert snap["debugChecked"] is False
    assert snap["chain"] is False


@given("会话创建失败的洞察助手")
def session_fails(world):
    _call(world, {"op": "reset", "sessionFail": True})


@given("已经完成一次洞察结论")
def concluded(world):
    _call(world, {"op": "conclude"})
    assert "发现" in world["snap"]["body"]


@given("改绑接口会失败")
def patch_fails(world):
    _call(world, {"op": "flags", "patchFail": True})


@when(parsers.parse("用户发送「{text}」而回复尚未返回"))
def send_pending(world, text):
    _call(world, {"op": "send", "text": text})
    assert world["snap"]["pending"] == 1


@when("用户发送空白")
def send_blank(world):
    _call(world, {"op": "blank"})


@when("运行返回结论和真实事件")
@when("继续请求返回结论和真实事件")
def release_success(world):
    _call(world, {"op": "release", "kind": "success"})


@when("运行返回结论但没有事件")
def release_no_events(world):
    _call(world, {"op": "release", "kind": "noevents"})


@when("运行返回结论但事件接口失败")
def release_runs_fail(world):
    _call(world, {"op": "release", "kind": "runsFail"})


@when("运行返回结论但事件接口无法解析")
def release_runs_bad(world):
    _call(world, {"op": "release", "kind": "runsBadJson"})


@when("运行以技术错误失败")
def release_failed(world):
    _call(world, {"op": "release", "kind": "failed"})


@when("运行完成但没有正文")
def release_empty(world):
    _call(world, {"op": "release", "kind": "empty"})


@when("消息接口失败")
@when("继续请求失败")
def release_http(world):
    _call(world, {"op": "release", "kind": "httpFail"})


@when("运行停在等确认")
def release_hitl(world):
    _call(world, {"op": "release", "kind": "hitl"})


@when("拒绝请求返回已拒绝")
def release_rejected(world):
    _call(world, {"op": "release", "kind": "rejected"})


@when("作者打开调试开关")
def enable_debug(world):
    _call(world, {"op": "debug", "on": True})
    assert world["snap"]["debugChecked"] is True


@when(parsers.parse("用户点选时间窗「{label}」"))
def click_chip(world, label):
    _call(world, {"op": "chip", "label": label})


@when(parsers.parse("用户点「{label}」"))
def click_button(world, label):
    world["posts_before_click"] = (world.get("snap") or {}).get("messagePosts")
    _call(world, {"op": "click", "text": label})


@when("用户点到时间窗标签")
def click_label(world):
    _call(world, {"op": "label"})


@then("进度条带已经出现并带有呼吸")
def strip_breathing(world):
    snap = world["snap"]
    assert snap["stripCount"] == 1
    assert snap["breathe"] is True
    assert snap["styleHasBreathe"] is True


@then("进度文案是「处理中」")
def progress_immediate(world):
    assert world["snap"]["progress"] == "处理中"
    assert world["snap"]["updates"] == ["处理中"]


@then("进度条带只有一条")
def one_strip(world):
    assert world["snap"]["stripCount"] == 1
    assert world["snap"]["maxConcurrentStrips"] == 1


@then("终端没有技术串")
def no_leaks(world):
    assert world["snap"]["leaks"] == []


@then("进度文案依次经过「思考中」「正在查询数据」「正在整理结论」")
def progress_sequence(world):
    assert world["snap"]["updates"] == PROGRESS_SEQUENCE
    assert all(item in WHITELIST for item in world["snap"]["updates"])


@then("进度更新发生在同一条条带上")
def same_strip(world):
    snap = world["snap"]
    assert snap["stripsThisTurn"] == 1
    assert snap["maxConcurrentStrips"] == 1
    assert len(snap["updates"]) >= 2


@then("结束后进度条带收起并留下结论")
def conclusion_visible(world):
    snap = world["snap"]
    assert snap["stripCount"] == 0
    assert "发现" in snap["body"]
    assert "销量" in snap["body"]


@then("助手正文里的标记仍是文本")
def markdown_not_html(world):
    assert world["snap"]["imgCount"] == 0


@then("进度文案始终不含「正在查询数据」")
def never_fake_tool_step(world):
    assert "正在查询数据" not in world["snap"]["updates"]
    assert world["snap"]["progress"] != "正在查询数据"


@then("页面没有调试链路")
def no_debug_chain(world):
    snap = world["snap"]
    assert snap["chain"] is False
    assert "调试链路" not in snap["body"]
    assert "Agent 链路" not in snap["body"]


@then("调试链路只包含白名单短句")
def debug_whitelist_only(world):
    snap = world["snap"]
    assert snap["chain"] is True
    lines = snap["chainLines"]
    assert lines
    assert all(line in WHITELIST for line in lines)
    assert "暂无更多进度" not in lines


@then("调试链路显示暂无更多进度")
def debug_empty(world):
    assert world["snap"]["chainLines"] == ["暂无更多进度"]
    assert world["snap"]["leaks"] == []


@then(parsers.parse("页面出现「{text}」"))
def body_contains(world, text):
    assert text in world["snap"]["body"]


@then("进度条带没有出现")
def no_strip(world):
    assert world["snap"]["stripCount"] == 0
    assert world["snap"]["messagePosts"] == 0


@then("进度文案经过「等确认」")
def saw_wait(world):
    assert "等确认" in world["snap"]["updates"]
    assert world["snap"]["updates"][-1] == "等确认"


@then("进度条带已收起")
def strip_gone(world):
    assert world["snap"]["stripCount"] == 0


@then(parsers.parse("出现按钮「{label}」"))
def has_button(world, label):
    assert label in world["snap"]["buttons"]


@then(parsers.parse("出现一行复述且包含「{text}」"))
def one_line_restate(world, text):
    line = world["snap"]["rebind"]
    assert text in line
    assert "\n" not in line
    assert "变了什么" not in line


@then(parsers.parse("出现按钮「{left}」和「{right}」"))
def two_buttons(world, left, right):
    buttons = world["snap"]["buttons"]
    assert left in buttons
    assert right in buttons


@then("页面没有并排对照")
def no_diff(world):
    snap = world["snap"]
    assert snap["diff"] is False
    assert "变了什么" not in snap["body"]
    assert "对照" not in snap["body"]


@then("时间窗芯片只有近90天、近30天、近7天")
def only_time_chips(world):
    assert world["snap"]["chips"] == ["近90天", "近30天", "近7天"]
    assert world["snap"]["datasetChips"] == 0


@then("再次出现进度条带")
def strip_again(world):
    snap = world["snap"]
    assert snap["stripCount"] == 1
    assert snap["breathe"] is True
    assert snap["progress"] in WHITELIST
    assert snap["maxConcurrentStrips"] == 1


@then(parsers.parse("当前时间窗是「{label}」"))
def current_window(world, label):
    snap = world["snap"]
    assert snap["activeChip"] == label
    assert f"时间窗 {label}" in snap["body"]


@then(parsers.parse("待发送的问题是「{text}」"))
def pending_exact(world, text):
    snap = world["snap"]
    assert snap["lastRequestText"] == text
    assert snap["pending"] == 1


@then("待发送的问题不是输入框占位符")
def pending_not_placeholder(world):
    snap = world["snap"]
    text = snap["lastRequestText"]
    assert text
    assert text != snap["placeholder"]
    assert "例如：" not in text
    assert not text.startswith("请基于电商销量数据")


@then("用户气泡里没有输入框占位符")
def bubble_not_placeholder(world):
    snap = world["snap"]
    last = snap["userTexts"][-1]
    assert last != snap["placeholder"]
    assert "例如：" not in last
    assert not last.startswith("请基于电商销量数据")


@then(parsers.parse("用户气泡是「{text}」"))
def user_bubble_is(world, text):
    assert text in world["snap"]["userTexts"]


@then("开场没有工程话术")
def no_engineering_copy(world):
    body = world["snap"]["body"]
    for word in ("Skill", "skill", "chip", "三拍", "退货风险"):
        assert word not in body, word


@then(parsers.parse("结论里没有「{text}」"))
def conclusion_without(world, text):
    assert text not in world["snap"]["body"]


@when("运行返回带技术串的结论")
def release_leaky(world):
    _call(world, {"op": "release", "kind": "leaky"})


_FIELD = {
    "类目": "category",
    "渠道": "channel",
    "省份": "province",
    "退货渠道": "channel",
}
_TOP = {
    ("category", "sum_gmv"): "电子产品",
    ("channel", "sum_gmv"): "抖音电商",
    ("province", "sum_gmv"): "江苏",
    ("channel", "return_rate"): "拼多多",
}


def _answer_tool(call: dict) -> dict:
    name = call.get("name") or ""
    args = call.get("arguments") or {}
    if name == "query_stats":
        key = _TOP.get((args.get("group_by"), args.get("metric")), "不该出现")
        return {"rows": [{"key": key, "value": 1.0}]}
    if name == "sample_records":
        filters = args.get("filters") or {}
        rec = {
            "order_id": "ORD1001",
            "category": filters.get("category", "美妆个护"),
            "channel": filters.get("channel", "京东"),
            "province": filters.get("province", "湖北"),
            "order_date": "2025-10-03",
            "gmv": 110.0,
        }
        return {"count": 2, "records": [rec, {**rec, "order_id": "ORD1002"}]}
    return {}


def _run_drill(text: str) -> dict:
    bindings = {
        "dataset": "ecommerce_sales",
        "time_window": {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"},
    }
    messages = [{"role": "user", "content": text}]
    tool_results: list[dict] = []
    samples: list[dict] = []
    filters: list[dict] = []
    allowed = list(SKILL["allowed_tools"])
    for _ in range(6):
        decision = stub_decide(
            messages=messages,
            allowed_tools=allowed,
            tool_results=tool_results,
            bindings=bindings,
        )
        if decision.get("type") == "final":
            return {
                "content": decision.get("content") or "",
                "samples": samples,
                "filters": filters,
            }
        assert decision.get("type") == "tool_calls", decision
        for call in decision.get("calls") or []:
            data = _answer_tool(call)
            if call.get("name") == "sample_records":
                filters.append((call.get("arguments") or {}).get("filters") or {})
                samples.extend(data.get("records") or [])
            tool_results.append(
                {
                    "name": call.get("name"),
                    "arguments": call.get("arguments") or {},
                    "data": data,
                    "evidence": {"fingerprint": "fixturefp"},
                }
            )
    raise AssertionError("drill did not finish")


@pytest.fixture
def drillbox() -> dict:
    return {}


@when(parsers.parse("分析师追问「{text}」"))
def ask_analyst(drillbox, text):
    drillbox.update(_run_drill(text))


@then(parsers.parse("依据标题是第{n}条"))
def drill_title(drillbox, n):
    assert f"第{n}条依据" in drillbox["content"]
    assert "不该出现" not in drillbox["content"]


@then(parsers.parse("抽出的样本都属于{dim}「{value}」"))
def drill_samples(drillbox, dim, value):
    field = _FIELD[dim]
    assert drillbox["samples"], drillbox["content"]
    assert all(item.get(field) == value for item in drillbox["samples"])
    assert drillbox["filters"], drillbox["content"]
    assert all(item.get(field) == value for item in drillbox["filters"])


@then(parsers.parse("依据说明写的是「{value}」"))
def drill_copy(drillbox, value):
    assert f"**{value}**" in drillbox["content"]


@then("页面没有确认卡片")
def no_card(world):
    assert world["snap"]["rebind"] == ""


@then("没有新的运行请求")
def no_new_run(world):
    assert world["snap"]["messagePosts"] == world["posts_before_click"]
