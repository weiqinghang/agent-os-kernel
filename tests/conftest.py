"""UI P0′ harness.

上游 `.playbook/docs/project/product-development-xp-contract.md` 不在本仓库，
按 agent-os-btdd 最小规则：可执行 Gherkin + 行覆盖 ≥ 90%。
产品行为在 `ui/chat`（App），不用 pytest-cov 去锁内核私有实现。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COVERAGE_TMP = ROOT / "coverage" / "tmp"
SUMMARY_PATH = ROOT / "coverage" / "coverage-summary.json"


class NodeClient:
    def __init__(self, proc: subprocess.Popen[str], stderr: list[str]) -> None:
        self.proc = proc
        self.stderr = stderr
        self._lock = threading.Lock()

    def call(self, msg: dict, timeout: float = 15) -> dict:
        with self._lock:
            if self.proc.poll() is not None:
                raise RuntimeError("node harness exited early\n" + "".join(self.stderr))
            assert self.proc.stdin is not None
            assert self.proc.stdout is not None
            self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            line = self._readline(timeout)
            data = json.loads(line)
            if not data.get("ok"):
                raise AssertionError(data.get("error") or data)
            return data

    def _readline(self, timeout: float) -> str:
        holder: list[str] = []

        def _read() -> None:
            assert self.proc.stdout is not None
            holder.append(self.proc.stdout.readline())

        thread = threading.Thread(target=_read, daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError("node harness timed out\n" + "".join(self.stderr[-40:]))
        if not holder or not holder[0]:
            raise RuntimeError("node harness closed\n" + "".join(self.stderr[-40:]))
        return holder[0]

    def close(self) -> None:
        if self.proc.poll() is not None:
            return
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def _coverage_gate() -> None:
    report = subprocess.run(
        [
            "npx",
            "c8",
            "report",
            "--temp-directory",
            str(COVERAGE_TMP),
            "--include",
            "ui/chat/**/*.js",
            "--reporter=text",
            "--reporter=json-summary",
            "--report-dir",
            str(ROOT / "coverage"),
            "--all",
            "--exclude-after-remap",
            "--check-coverage",
            "--lines",
            "90",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    text = (report.stdout or "") + (report.stderr or "")
    print(text)
    if report.returncode != 0:
        pytest.exit("ui/chat line coverage below 90%\n" + text, returncode=1)


@pytest.fixture(scope="session")
def node_client() -> NodeClient:
    COVERAGE_TMP.mkdir(parents=True, exist_ok=True)
    for old in COVERAGE_TMP.glob("*.json"):
        old.unlink()
    env = os.environ.copy()
    env["NODE_V8_COVERAGE"] = str(COVERAGE_TMP)
    proc = subprocess.Popen(
        ["node", "tests/ui_harness.mjs"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        bufsize=1,
    )
    stderr: list[str] = []

    def _drain() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr.append(line)

    threading.Thread(target=_drain, daemon=True).start()
    client = NodeClient(proc, stderr)
    try:
        yield client
    finally:
        client.close()
        _coverage_gate()


@pytest.fixture
def world(node_client: NodeClient) -> dict:
    box = {"node": node_client, "snap": None, "posts_before_click": None}
    box["snap"] = node_client.call({"op": "reset"})
    return box
