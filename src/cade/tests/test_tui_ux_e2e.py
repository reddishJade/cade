"""在真实终端中验证工作台布局、补全和窄屏操作，并保存重现证据。"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
from rich.console import Console
from rich.text import Text


class TuiTerminal:
    """保存终端输入步骤和每次检查的原始画面。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.socket = f"cade-ux-{uuid4().hex[:12]}"
        self.steps: list[list[str]] = []

    def run(self, *args: str) -> str:
        self.steps.append(list(args))
        return subprocess.run(
            ["tmux", "-L", self.socket, *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout

    def screen(self) -> str:
        return self.run("capture-pane", "-p", "-t", "ui:0.0")

    def save(self, name: str) -> str:
        plain = self.screen()
        ansi = self.run("capture-pane", "-p", "-e", "-N", "-t", "ui:0.0")
        (self.root / f"{name}.txt").write_text(plain, encoding="utf-8")
        (self.root / f"{name}.ansi").write_text(ansi, encoding="utf-8")
        console = Console(record=True, file=StringIO(), width=120)
        console.print(Text.from_ansi(ansi), end="", soft_wrap=True)
        console.save_html(str(self.root / f"{name}.html"))
        return plain

    def wait(self, expected: str, *, absent: str = "") -> str:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            screen = self.screen()
            if expected in screen and (not absent or absent not in screen):
                return screen
            time.sleep(0.05)
        raise AssertionError(self.save("timeout"))

    def keys(self, *keys: str) -> None:
        self.run("send-keys", "-t", "ui:0.0", *keys)

    def paste(self, text: str) -> None:
        path = self.root / "paste.txt"
        path.write_text(text, encoding="utf-8")
        self.run("load-buffer", str(path))
        self.run("paste-buffer", "-p", "-t", "ui:0.0")
        self.wait(text.splitlines()[-1])


@pytest.fixture
def ux_terminal(tmp_path: Path) -> Iterator[TuiTerminal]:
    if shutil.which("tmux") is None:
        pytest.skip("终端 E2E 需要 tmux")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "provider": {
                    "model_profiles": {
                        "main": {
                            "transport": "deepseek_chat",
                            "chat_model": "terminal-ux-model",
                            "api_key": "terminal-only-no-model-requests",
                            "base_url": "http://127.0.0.1:9",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# 终端验证\n", encoding="utf-8")
    command = shlex.join(
        [
            "env",
            "-u",
            "NO_COLOR",
            "TERM=xterm-256color",
            "COLORTERM=truecolor",
            "PROMPT_TOOLKIT_COLOR_DEPTH=DEPTH_24_BIT",
            sys.executable,
            "-m",
            "cade",
            "--project-root",
            str(tmp_path),
            "--config",
            str(config),
        ]
    )
    terminal = TuiTerminal(tmp_path)
    terminal.run(
        "new-session",
        "-d",
        "-s",
        "ui",
        "-x",
        "120",
        "-y",
        "42",
        "-c",
        str(tmp_path),
        command,
    )
    try:
        terminal.wait("terminal-ux-model")
        yield terminal
    finally:
        (tmp_path / "reproduce.json").write_text(
            json.dumps(
                {"command": command, "steps": terminal.steps},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        terminal.run("kill-server")


def assert_footer_and_input(screen: str, *, width: int) -> None:
    """输入边界和状态区必须完整，不被菜单或换行覆盖。"""
    lines = screen.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith(">"))
    assert set(lines[index - 1].strip()) == {"─"}
    assert any("context:" in line for line in lines[index + 1 :])
    assert any("mode:" in line for line in lines[index + 1 :])
    assert all(len(line) <= width for line in lines)


def test_real_terminal_completion_and_responsive_layout(
    ux_terminal: TuiTerminal,
) -> None:
    terminal = ux_terminal
    screen = terminal.save("01-welcome")
    assert "Describe a task" in screen
    assert "Ctrl+O tool details" in screen
    assert_footer_and_input(screen, width=120)

    terminal.keys("/")
    terminal.wait("Start a new session")
    screen = terminal.save("02-commands")
    assert "48;2;33;38;45" in (terminal.root / "02-commands.ansi").read_text()
    assert_footer_and_input(screen, width=120)
    assert screen.index("Start a new session") < screen.index("> /")
    terminal.keys("Down", "Enter")
    terminal.wait("> /new", absent="Start a new session")
    terminal.save("03-completion-accepted")
    terminal.keys("C-c")
    terminal.wait("\n>\n", absent="> /new")

    terminal.paste("@READ")
    terminal.wait("README.md")
    assert_footer_and_input(terminal.save("04-files"), width=120)
    terminal.keys("Escape")
    terminal.wait("> @READ", absent="README.md")
    terminal.keys("C-c")
    terminal.wait("\n>\n", absent="@READ")
    terminal.paste("first line\n第二行")
    screen = terminal.save("05-multiline")
    assert "first line" in screen and "第二行" in screen
    assert "terminal-ux-model" in screen
    terminal.keys("C-c")
    terminal.wait("\n>\n", absent="第二行")

    terminal.run("resize-window", "-t", "ui:0", "-x", "60", "-y", "16")
    terminal.paste("@READ")
    terminal.wait("README.md")
    assert_footer_and_input(terminal.save("06-narrow"), width=60)
    terminal.keys("Escape", "C-c")
    terminal.wait("\n>\n", absent="@READ")
    terminal.paste("/pla")
    terminal.wait("Enter Plan Mode")
    terminal.keys("Enter")
    terminal.wait("> /plan", absent="Enter Plan Mode")
    terminal.keys("Enter")
    terminal.wait("mode: plan")
    terminal.save("07-mode")
    terminal.paste("/mod")
    terminal.wait("Show current model info")
    terminal.keys("Enter")
    terminal.wait("> /model", absent="Show current model info")
    terminal.keys("Enter")
    terminal.wait("Enter a custom model name")
    terminal.save("08-model-picker")
    terminal.keys("Escape")
    terminal.wait("\n>\n", absent="Enter a custom model name")
    terminal.save("09-picker-cancelled")
    terminal.keys("?")
    terminal.wait("utput\n")
    terminal.run("resize-window", "-t", "ui:0", "-x", "60", "-y", "12")
    terminal.wait("utput\n")
    terminal.save("10-resize-follows-latest")
