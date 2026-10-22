"""P0′ 产品行为：进度条带、脱敏、时间窗一行确认，以及 UAT 三件必须改。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from agent_os_kernel import model_broker, obs, orchestrator, run, session
from agent_os_kernel.skills.insight_report import PLAN_PROGRESS, SKILL, apply_plan_progress, stub_decide
from agent_os_kernel.tool_gateway import ToolGateway

scenarios(str(Path(__file__).parent / "features" / "ui_p0_prime.feature"))
scenarios(str(Path(__file__).parent / "features" / "midflight_progress.feature"))
scenarios(str(Path(__file__).parent / "features" / "cancel_progress.feature"))
scenarios(str(Path(__file__).parent / "features" / "insight_plan_card.feature"))
scenarios(str(Path(__file__).parent / "features" / "failed_run_retry_ui.feature"))
scenarios(str(Path(__file__).parent / "features" / "plan_card_progress.feature"))

WHITELIST = {"处理中", "思考中", "正在查询数据", "正在整理结论", "等确认"}
PLAN_LABELS = [
    "确认这次要看的范围",
    "看清类目卖得怎么样",
    "看清渠道结构",
    "看清各省贡献",
    "看清退货风险",
]
PROGRESS_SEQUENCE = ["处理中", "思考中", "正在查询数据", "正在整理结论"]


def _call(world, msg):
    world["snap"] = world["node"].call(msg)
    return world["snap"]


@given("调试关闭的洞察助手")
def open_ui(world):
    snap = world["snap"]
    assert snap["debugChecked"] is False
    assert snap["chain"] is False
    assert snap["debugVisible"] is False


@given("地址栏带有调试入口的洞察助手")
def open_debug_entry(world):
    snap = _call(world, {"op": "reset", "url": "http://127.0.0.1:8787/?debug=1"})
    world["snap"] = snap
    assert snap["debugVisible"] is True
    assert snap["debugChecked"] is False


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


@then("进度文案始终不含「正在整理结论」")
def never_writing_copy(world):
    assert "正在整理结论" not in world["snap"]["updates"]
    assert world["snap"]["progress"] != "正在整理结论"


@then("调试开关不可见")
def debug_hidden(world):
    assert world["snap"]["debugVisible"] is False
    assert "调试" not in world["snap"]["buttons"]


@then("调试开关可见")
def debug_shown(world):
    assert world["snap"]["debugVisible"] is True


@then("调试开关仍是关闭")
def debug_still_off(world):
    assert world["snap"]["debugChecked"] is False
    assert world["snap"]["chain"] is False


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


@when("服务接受回合并在后台继续")
def accept_turn(world):
    _call(world, {"op": "accept"})
    assert world["snap"]["pending"] == 0


@when("后台先出现思考事件")
def push_think(world):
    _call(world, {"op": "push", "kind": "think"})


@when("后台再出现查数事件")
def push_tool(world):
    _call(world, {"op": "push", "kind": "tool"})


@when("后台出现计划事件")
@when("后台又出现一次计划事件")
def push_plan(world):
    _call(world, {"op": "push", "kind": "plan"})


@when("后台给出结论")
def finish_success(world):
    _call(world, {"op": "finish", "kind": "success"})


@when("后台给出结论但没有查数事件")
def finish_without_tool(world):
    _call(world, {"op": "finish", "kind": "notool"})


@when("服务接受回合但事件轮询失败")
def accept_poll_fails(world):
    _call(world, {"op": "accept", "runsFail": True})


@when("服务接受回合并停在进行中")
def accept_stuck(world):
    _call(world, {"op": "accept", "pollBudgetMs": 0})


@then("进度条带仍然是这一条")
def still_same_strip(world):
    snap = world["snap"]
    assert snap["stripCount"] == 1
    assert snap["stripsThisTurn"] == 1
    assert snap["pending"] == 0


@then("进度文案是「思考中」")
def progress_thinking(world):
    assert world["snap"]["progress"] == "思考中"
    assert "正在查询数据" not in world["snap"]["updates"]


@then("进度文案是「正在查询数据」")
def progress_query(world):
    assert world["snap"]["progress"] == "正在查询数据"


@then("进度文案是「正在整理结论」")
def progress_writing(world):
    assert world["snap"]["progress"] == "正在整理结论"
    assert world["snap"]["updates"][-1] == "正在整理结论"


@then("计划卡列出中文步骤")
@then("计划卡仍然列出中文步骤")
def plan_card_labels(world):
    snap = world["snap"]
    assert snap["planTag"] == "DETAILS"
    assert snap["planLabels"] == PLAN_LABELS
    assert snap["planSummary"] == ["报告大纲"] * snap["planCount"]
    assert all(snap["planOpen"])
    for label in PLAN_LABELS:
        assert label in snap["body"]
    assert "多出来的收尾" not in snap["body"]


@then("计划卡仍然只有一张")
def one_plan_card(world):
    assert world["snap"]["planCount"] == 1


@then("进度文案仍在白名单内")
def progress_stays_whitelisted(world):
    snap = world["snap"]
    assert snap["progress"] in WHITELIST
    assert all(item in WHITELIST for item in snap["updates"])
    assert all(label not in WHITELIST for label in snap["planLabels"])


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


@then(parsers.parse("页面没有按钮「{label}」"))
def no_button(world, label):
    assert label not in world["snap"]["buttons"]
    assert label not in world["snap"]["body"]


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


@when("运行返回带样本编号的依据")
def release_drill_view(world):
    _call(world, {"op": "release", "kind": "drill"})


@when("后台进入收尾整理")
def push_writing(world):
    _call(world, {"op": "push", "kind": "writing"})


@when("后台出现未注册的阶段事件")
def push_phase(world):
    _call(world, {"op": "push", "kind": "phase"})


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


@then("页面没有停止按钮")
def no_stop_button(world):
    assert world["snap"]["stopVisible"] is False
    assert "停止" not in world["snap"]["buttons"]


@then("已发出取消请求")
def cancel_posted(world):
    snap = world["snap"]
    assert snap["cancelPosts"] == 1
    assert "/cancel" in snap["cancelUrl"]
    assert snap["stopVisible"] is True


@when("后台确认已经停止")
def ack_stopped(world):
    _call(world, {"op": "ack-cancel"})


@then("轮询已经停住")
def polls_stopped(world):
    assert world["snap"]["pollsStable"] is True


@then("停止按钮已经收起")
def stop_hidden(world):
    assert world["snap"]["stopVisible"] is False
    assert "停止" not in world["snap"]["buttons"]
    assert world["snap"]["stripCount"] == 0


@then("已向失败回合发出重试")
def retry_posted(world):
    snap = world["snap"]
    assert snap["retryPosts"] == 1
    assert snap["messagePosts"] == 1
    assert snap["resumePosts"] == 0
    assert snap["retryOf"]
    assert snap["retryUrl"].endswith("/runs/" + snap["retryOf"] + "/retry")
    assert "resume_token" not in snap["retryBody"]
    assert "请重试上一轮。" in snap["userTexts"]


@when("重试被接受并在新回合给出结论")
def release_retry(world):
    _call(world, {"op": "release-retry"})


@then("进度改跟新的回合")
def followed_new_run(world):
    snap = world["snap"]
    assert snap["followedRunId"]
    assert snap["retryOf"]
    assert snap["followedRunId"] != snap["retryOf"]
    assert snap["eventUrls"]
    assert snap["followedRunId"] in snap["eventUrls"][-1]


@when("重试请求被拒绝")
def retry_rejected(world):
    _call(world, {"op": "retry-fail"})


@then("没有改走确认接口")
def not_resume(world):
    assert world["snap"]["resumePosts"] == 0
    assert world["snap"]["retryPosts"] == 1


_PLAN_SECRETS = (
    "list_datasets",
    "describe_dataset",
    "describe_fields",
    "describe_tag_hierarchies",
    "query_stats",
    "search_records",
    "sample_records",
    "phase_",
    "fingerprint",
    "pending",
    "active",
    "done",
    "skipped",
    "stub",
    "live",
)


@when("后台出现一次失败的查数")
def push_failed_tool(world):
    _call(world, {"op": "push", "kind": "failtool"})


@when("后台再出现三次成功的查数")
def push_three_tools(world):
    for _ in range(3):
        _call(world, {"op": "push", "kind": "tool"})


@when("后台出现带结论的计划")
def push_conclusion(world):
    _call(world, {"op": "push", "kind": "conclusion"})


@when("后台记下已经取消")
def push_cancelled(world):
    _call(world, {"op": "push", "kind": "stopped"})


@then("计划卡只显示步骤和状态")
def plan_label_and_status_only(world):
    snap = world["snap"]
    assert snap["planLabels"]
    assert snap["planStatusText"]
    assert set(snap["planStatusText"]) <= {"未开始", "进行中", "完成", "跳过"}
    blob = "\n".join(snap["planLabels"] + snap["planStatusText"])
    for secret in _PLAN_SECRETS:
        assert secret not in blob
    assert "query_stats" not in snap["body"]


@then("计划卡第一步在进行且其余未开始")
def first_active(world):
    assert world["snap"]["planStatuses"] == ["active", "pending", "pending", "pending", "pending"]
    assert world["snap"]["planStatusText"][0] == "进行中"


@then("计划卡第一步已完成且下一步在进行")
def first_done_next_active(world):
    assert world["snap"]["planStatuses"][:2] == ["done", "active"]
    assert world["snap"]["planStatusText"][:2] == ["完成", "进行中"]


@then("计划卡前两步已完成且第三步在进行")
def two_done_third_active(world):
    assert world["snap"]["planStatuses"][:3] == ["done", "done", "active"]


@then("计划卡步骤全部完成")
def plan_all_done(world):
    assert world["snap"]["planStatuses"] == ["done", "done", "done", "done", "done"]
    assert set(world["snap"]["planStatusText"]) == {"完成"}


@then("计划卡步骤全部未开始")
def plan_all_pending(world):
    assert world["snap"]["planStatuses"]
    assert set(world["snap"]["planStatuses"]) == {"pending"}
    assert set(world["snap"]["planStatusText"]) == {"未开始"}


@then("事件里写的完成状态没有被照抄")
def event_status_ignored(world):
    snap = world["snap"]
    assert snap["planLabels"][0] == "确认这次要看的范围"
    assert snap["planStatuses"][0] == "active"
    write = snap["planLabels"].index("整理结论")
    assert snap["planStatuses"][write] == "pending"


@then("结论步骤是进行中")
def conclusion_active(world):
    snap = world["snap"]
    write = snap["planLabels"].index("整理结论")
    assert snap["planStatuses"][write] == "active"
    assert snap["planStatusText"][write] == "进行中"


@then("计划卡已完成的步骤仍是完成")
def done_stays_done(world):
    assert world["snap"]["planStatuses"][0] == "done"
    assert world["snap"]["planStatusText"][0] == "完成"


@then("计划卡没做完的步骤是跳过")
def unfinished_skipped(world):
    rest = world["snap"]["planStatuses"][1:]
    assert rest
    assert set(rest) == {"skipped"}
    assert "跳过" in world["snap"]["planStatusText"]


@pytest.fixture
def kernel(monkeypatch, tmp_path):
    monkeypatch.setattr(obs, "DB_PATH", tmp_path / "kernel.db")
    monkeypatch.setattr(model_broker, "has_llm", lambda: False)
    for key in ("MODEL_API_KEY", "XAI_API_KEY", "OPENAI_API_KEY", "MODEL_BASE_URL", "MODEL_NAME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HITL_AUTO", "1")

    def call(self, name, arguments=None, *, bindings=None):
        del self, name, arguments, bindings
        return {
            "data": {
                "rows": [{"key": "电子产品", "value": 1}],
                "records": [],
                "datasets": [{"name": "ecommerce_sales_v2"}],
                "row_count": 1,
                "summary": {"total_gmv": 1, "min_date": "a", "max_date": "b", "return_pct": 0},
            },
            "evidence": {"fingerprint": "fpxxxx", "tool": "query_stats"},
        }

    monkeypatch.setattr(ToolGateway, "call", call)
    obs.init_db()
    return {}


@given("自动确认的洞察回合")
def given_auto_kernel(kernel):
    assert SKILL["plan_progress"] == PLAN_PROGRESS
    blob = json.dumps(PLAN_PROGRESS, ensure_ascii=False)
    for name in ("query_stats", "sample_records", "list_datasets", "describe_dataset"):
        assert name not in blob
    bindings = {
        "skill": "insight_report",
        "dataset": "ecommerce_sales_v2",
        "time_window": {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"},
    }
    sid = session.create_session({"bindings": bindings})["id"]
    session.add_message(sid, "user", "请基于电商销量数据写一份简短洞察报告")
    kernel["sid"] = sid
    kernel["rid"] = run.create_run(sid, context_bindings=bindings)["id"]


@when("这份洞察回合跑完")
def run_auto_kernel(kernel):
    kernel["result"] = orchestrator.handle_user_message(
        kernel["sid"],
        kernel["rid"],
        "请基于电商销量数据写一份简短洞察报告",
    )


@then("落库的事件种类没有 phase_ 前缀")
def no_phase_kinds(kernel):
    kinds = [event["kind"] for event in obs.list_events(kernel["rid"])]
    assert kinds
    assert not any(str(kind).startswith("phase_") for kind in kinds)
    assert kernel["result"]["status"] == "completed"


@then("落库的计划步骤仍都是未开始")
def stored_plan_pending(kernel):
    plans = [event for event in obs.list_events(kernel["rid"]) if event["kind"] == "plan"]
    assert len(plans) == 1
    payload = plans[0]["payload"]
    assert set(payload) == {"revision", "steps"}
    assert payload["revision"] == 1
    assert all(step["status"] == "pending" for step in payload["steps"])


@then("同一批事件按规则能算出已完成的步骤")
def counted_done(kernel):
    events = obs.list_events(kernel["rid"])
    names = []
    oks = 0
    for event in events:
        if event["kind"] != "tool_call":
            continue
        payload = event["payload"] if isinstance(event["payload"], dict) else {}
        if payload.get("ok") is True:
            oks += 1
        tool = payload.get("tool")
        if isinstance(tool, str):
            names.append(tool)
    assert oks >= 5
    assert len(set(names)) >= 2
    progress = apply_plan_progress(SKILL["plan_steps"], events)
    assert [step["status"] for step in progress] == ["done"] * len(SKILL["plan_steps"])
    blob = json.dumps(progress, ensure_ascii=False)
    for name in names:
        assert name not in blob
