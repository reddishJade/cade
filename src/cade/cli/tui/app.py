"""TUI 应用入口：_CadeTui 主类。"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import threading
from asyncio import TimerHandle
from collections.abc import Callable, Sequence
from contextlib import redirect_stdout
from importlib.metadata import version
from io import StringIO
from pathlib import Path
from queue import Empty, Queue
from threading import Event
from time import perf_counter
from typing import TYPE_CHECKING, cast

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import (
    AnyFormattedText,
    FormattedText,
    StyleAndTextTuples,
)
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (
    ConditionalContainer,
    Dimension,
    HSplit,
    Layout,
    ScrollOffsets,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output.base import Output
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import CheckboxList, RadioList, TextArea

from cade.agent.messages import UserMessage
from cade.coding_agent.tools.question import CUSTOM_OPTION_LABEL
from cade.harness.agent_runtime import AgentHarnessEvent, BusyMessageMode, SubmitStatus
from cade.harness.agent_runtime.events import (
    FinalStructuredEvent,
    ToolUseStructuredEvent,
)
from cade.harness.snapshot import SnapshotStore, SnapshotUnsupportedError

from ..app_contract import ReplApp
from ..commands import ReplState
from ..completion import CommandArgsSuggester, ReplCompleter
from ..config_registry import SettingSpec
from ..exit_keys import ExitKeyKind, exit_confirm_hint, exit_confirmed
from ..file_refs import expand_file_references
from ..git import git_branch_name
from ..markdown import TerminalMarkdownRenderer
from ..repl import current_effort_options, current_model_options
from ..repl_commands import COMMAND_NAMES, COMMAND_REGISTRY_EXPORT, handle_command
from ..repl_hitl import (
    HITL_CHOICES,
    approval_scope_lines,
    hitl_choices,
    parse_hitl_choice,
    tool_preview_lines,
)
from ..repl_sessions import (
    select_session_interactively,
)
from ..repl_skills import activate_skill, available_skill_names, parse_skill_invocation
from ..repl_tools import (
    file_reference_event,
    run_shell_shortcut,
)
from ..shared.working import working_status_text
from .chrome import TUI_STYLES, compact_path, fit_text, status_line, welcome_text
from .exit_summary import print_exit_summary
from .state import (
    _CommandChoiceRequest,
    _CommandTextRequest,
    _HitlRequest,
    _LogEntry,
    _QuestionChoiceRequest,
    _TuiState,
)
from .widgets import (
    TuiCompletionControl,
    TuiInputLexer,
    TuiPromptSession,
    tui_input_prompt,
)

logger = logging.getLogger(__name__)

_SHORTCUT_HELP = """Shortcuts
  ?              show this help
  Ctrl+C         clear input; interrupt; press Ctrl+C twice to exit when idle
  Ctrl+D         press Ctrl+D twice to exit when idle (empty input)
  Ctrl+Q         exit
  Shift+Tab      cycle execution mode: act → build → plan
  Ctrl+T         expand or collapse thinking
  Ctrl+O         expand or collapse tool details
  ! command      run a bash command
  $skill [task]  invoke a skill
  /command       run a slash command
  Tab            complete commands, skills, and @file references
  Enter          send; queue the next task while running
  Alt+Enter      steer the current task while running
  Ctrl+J         insert a newline (Shift+Enter where supported)
  Esc Enter      newline when idle; steer while running
  PageUp/Down    scroll history; End returns to the latest output"""

if TYPE_CHECKING:
    from prompt_toolkit.history import History

    from cade.agent.types import ApprovalRequest
    from cade.harness.security import HITLResult
    from cade.harness.security.permission_model import (
        SessionGrantStoreManager,
    )
    from cade.harness.snapshot import SnapshotResult, SnapshotService


def run_tui(
    app: ReplApp,
    project_root: Path,
    *,
    resume_latest: bool = False,
    auto_continue: bool = False,
    session_id: str | None = None,
    config_path: Path | None = None,
) -> int:
    try:
        return _CadeTui(
            app,
            project_root,
            resume_latest=resume_latest,
            auto_continue=auto_continue,
            session_id=session_id,
            config_path=config_path,
        ).run()
    except KeyboardInterrupt:
        return 0
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1


class _CadeTui:
    _STREAM_REFRESH_INTERVAL = 0.05
    _EVENT_DRAIN_TIMEOUT = 5.0

    def __init__(
        self,
        app: ReplApp,
        project_root: Path,
        input: Input | None = None,
        output: Output | None = None,
        *,
        resume_latest: bool = False,
        auto_continue: bool = False,
        session_id: str | None = None,
        config_path: Path | None = None,
    ) -> None:
        self._agent_app = app
        self._project_root = project_root
        self._config_path = config_path
        self._store = app.session_store
        self._welcome_version = version("cade-agent")
        self._welcome_tools = tuple(tool.name for tool in app.registry)
        self._welcome_skills = available_skill_names(app)
        instruction_paths = getattr(app, "instruction_paths", None)
        paths = instruction_paths() if callable(instruction_paths) else ()
        self._welcome_instructions: tuple[Path, ...] = (
            tuple(path for path in paths if isinstance(path, Path))
            if isinstance(paths, tuple)
            else ()
        )
        self._repl_state = ReplState()
        self._repl_state.busy_mode = BusyMessageMode.FOLLOW_UP
        self._startup_notice = ""
        self._snapshot_store = _init_snapshot_store(project_root)
        self._state = _TuiState(mode=self._repl_state.mode, project_root=project_root)
        self._state.thinking_collapsed = True
        self._state.tool_collapsed = True
        self._repl_state.thinking_collapsed = True
        self._repl_state.tool_collapsed = True
        from cade.harness.security.permission_model import FileGrantStore

        self._permanent_grant_store = FileGrantStore.for_project_root(project_root)
        self._restore_startup_session(resume_latest, auto_continue, session_id)
        self._sync_mode_from_agent()
        self._workspace_branch = git_branch_name(project_root)
        self._scrollback = 0
        self._committing = False
        self._grant_store_manager: SessionGrantStoreManager | None = None
        try:
            from cade.harness.security.permission_model import (
                SessionGrantStoreManager,
            )

            self._grant_store_manager = SessionGrantStoreManager()
        except ImportError:
            pass
        self._markdown_renderer = TerminalMarkdownRenderer()
        self._prompt_session = TuiPromptSession()
        self._awaiting_denial_suggestion = False
        self._exit_pending = 0.0
        self._exit_pending_key = ""
        self._last_stream_refresh = 0.0
        self._stream_refresh_handle: TimerHandle | None = None
        self._working_refresh_handle: TimerHandle | None = None
        self._agent_event_queue: Queue[AgentHarnessEvent] = Queue()
        self._agent_event_queue_lock = threading.Lock()
        self._agent_event_drain_lock = threading.Lock()
        self._agent_event_drain_scheduled = False

        # ── UI 组件 ──
        self._output_control = FormattedTextControl(text="", focusable=False)
        self._output = Window(
            self._output_control,
            wrap_lines=True,
            always_hide_cursor=True,
            height=lambda: Dimension.exact(self._output_height()),
        )
        completer = self._make_completer()
        self._input = TextArea(
            height=lambda: Dimension.exact(self._input_height()),
            prompt=self._input_prompt,
            multiline=True,
            completer=completer,
            lexer=TuiInputLexer(),
            complete_while_typing=True,
            auto_suggest=CommandArgsSuggester(completer.command_args),
            history=_tui_history(project_root),
            scrollbar=False,
            style="class:input",
        )
        self._input.buffer.on_text_insert += self._on_input_text_inserted
        self._input.buffer.on_text_changed += self._on_input_layout_changed
        self._input.buffer.on_completions_changed += self._on_input_layout_changed
        self._completion_container = ConditionalContainer(
            Window(
                TuiCompletionControl(),
                height=lambda: Dimension.exact(self._completion_height()),
                scroll_offsets=ScrollOffsets(top=1, bottom=1),
                style="class:completion-menu",
            ),
            filter=Condition(lambda: self._completion_height() > 0),
        )
        self._approval_choices = RadioList(
            [(choice, choice) for choice in HITL_CHOICES],
            default=HITL_CHOICES[0],
            show_numbers=True,
            select_on_focus=True,
            open_character="",
            select_character="❯",
            close_character="",
            show_cursor=False,
            show_scrollbar=False,
        )
        approval_bindings = cast(
            KeyBindings, self._approval_choices.control.key_bindings
        )
        approval_bindings.add("enter")(lambda _event: self._accept_approval_choice())
        self._command_choices: RadioList[object] = RadioList(
            [(None, "")],
            show_numbers=True,
            select_on_focus=True,
            open_character="",
            select_character="❯",
            close_character="",
            show_cursor=False,
            show_scrollbar=False,
        )
        command_bindings = cast(KeyBindings, self._command_choices.control.key_bindings)
        command_bindings.add("enter")(lambda _event: self._accept_command_choice())
        # Esc 由菜单控件自身消费（eager 立即触发），返回上级或关闭。
        command_bindings.add("escape", eager=True)(
            lambda _event: self._cancel_key(None)
        )
        self._question_choices: RadioList[str] = RadioList(
            [("", "")],
            show_numbers=True,
            select_on_focus=True,
            open_character="",
            select_character="❯",
            close_character="",
            show_cursor=False,
            show_scrollbar=False,
        )
        question_bindings = cast(
            KeyBindings, self._question_choices.control.key_bindings
        )
        question_bindings.add("enter")(lambda _event: self._accept_question_choice())
        self._question_checkboxes: CheckboxList[str] = CheckboxList(
            [("", "")],
            open_character="",
            select_character="✓",
            close_character="",
        )
        checkbox_bindings = cast(
            KeyBindings, self._question_checkboxes.control.key_bindings
        )
        checkbox_bindings.add("enter")(lambda _event: self._accept_question_choice())
        self._approval_container = ConditionalContainer(
            HSplit(
                [self._approval_choices],
                height=lambda: Dimension.exact(self._approval_panel_height()),
            ),
            filter=Condition(
                lambda: (
                    self._state.pending_hitl is not None
                    and not self._awaiting_denial_suggestion
                )
            ),
        )
        self._command_container = ConditionalContainer(
            HSplit(
                [
                    self._command_choices,
                    Window(
                        FormattedTextControl(text=self._command_choice_hint_text),
                        height=1,
                    ),
                ],
                height=lambda: Dimension.exact(self._command_panel_height()),
            ),
            filter=Condition(lambda: self._state.pending_command_choice is not None),
        )
        self._question_container = ConditionalContainer(
            HSplit(
                [
                    Window(
                        FormattedTextControl(text=self._question_prompt_text),
                        height=1,
                    ),
                    ConditionalContainer(
                        self._question_choices,
                        filter=Condition(self._single_question_visible),
                    ),
                    ConditionalContainer(
                        self._question_checkboxes,
                        filter=Condition(self._multiple_question_visible),
                    ),
                ],
                height=lambda: Dimension.exact(self._question_panel_height()),
            ),
            filter=Condition(lambda: self._state.pending_question_choice is not None),
        )
        input_visible = Condition(
            lambda: (
                self._state.pending_command_choice is None
                and self._state.pending_question_choice is None
                and (
                    self._state.pending_hitl is None or self._awaiting_denial_suggestion
                )
            )
        )
        self._input_container = ConditionalContainer(
            HSplit(
                [
                    Window(height=1),
                    self._input,
                    Window(height=1),
                ],
                style="class:input",
            ),
            filter=input_visible,
        )
        self._status = Window(
            FormattedTextControl(text=self._status_text),
            height=2,
            style="class:status",
        )
        self._input_hint = Window(
            FormattedTextControl(text=self._input_hint_text),
            height=1,
            style="class:status",
        )
        # ── Application ──
        self._application = Application(
            layout=Layout(
                HSplit(
                    [
                        self._output,
                        self._approval_container,
                        self._command_container,
                        self._question_container,
                        self._completion_container,
                        self._input_container,
                        ConditionalContainer(
                            self._input_hint,
                            filter=Condition(lambda: self._input_hint_height() > 0),
                        ),
                        self._status,
                    ],
                ),
            ),
            key_bindings=self._bindings(),
            full_screen=False,
            erase_when_done=True,
            # 保留终端原生的鼠标选择、复制和右键行为；历史使用键盘翻页。
            mouse_support=False,
            enable_page_navigation_bindings=False,
            before_render=lambda _app: self._prepare_frame(),
            style=Style.from_dict(TUI_STYLES),
            input=input,
            output=output,
        )

        # ── Wire agent hooks ──
        agent = getattr(self._agent_app, "agent", None)
        if agent is not None:
            if getattr(agent, "approval_policy", "on-request") == "on-request":
                agent.user_approval_callback = self._approval_callback
            agent.session_id = self._store.session_id
            if hasattr(agent, "set_session_grant_store_provider"):
                agent.set_session_grant_store_provider(
                    lambda: (
                        self._grant_store_manager.get_for_session(
                            getattr(agent, "session_id", "tui")
                        )
                        if self._grant_store_manager is not None
                        else None
                    )
                )
            if hasattr(agent, "set_permanent_grant_store"):
                agent.set_permanent_grant_store(self._permanent_grant_store)
            from ..repl_commands import _compute_context_summary

            _compute_context_summary(agent, self._project_root, self._repl_state)

        from cade.coding_agent.tools.question import set_question_prompt_handler

        for tool in self._agent_app.registry:
            if tool.name == "question":
                set_question_prompt_handler(tool, self._question_prompt_callback)
                break

        if not self._state.log:
            self._state.log.append(_LogEntry("welcome", self._welcome_text()))
        if self._startup_notice:
            self._state.log.append(_LogEntry("system", self._startup_notice))
        self._application.layout.focus(self._input)
        self._refresh()

    def run(self) -> int:
        started_at = perf_counter()
        try:
            self._application.run()
        finally:
            print_exit_summary(
                self._store,
                perf_counter() - started_at,
                self._project_root,
                self._config_path,
            )
        return 0

    # ── 辅助 ──

    def _make_completer(self) -> ReplCompleter:
        registry = tuple(getattr(self._agent_app, "registry", ()) or ())
        return ReplCompleter(
            project_root=self._project_root,
            registry=registry,
            command_names=COMMAND_NAMES,
            command_registry=COMMAND_REGISTRY_EXPORT,
            effort_options=lambda: current_effort_options(self._agent_app),
            model_options=lambda: current_model_options(self._agent_app),
            skill_options=lambda: available_skill_names(self._agent_app),
        )

    def _input_prompt(self) -> FormattedText:
        request = self._state.pending_command_text
        if request is not None:
            return FormattedText([("", f"{request.prompt} > ")])
        return FormattedText(
            tui_input_prompt(
                self._awaiting_denial_suggestion,
                self._input.text.startswith("!"),
            )
        )

    def _question_prompt_text(self) -> str:
        request = self._state.pending_question_choice
        if request is None:
            return ""
        hint = (
            " (Space to toggle, Enter to submit)"
            if request.multiple
            else " (Use arrow keys)"
        )
        return f"? {request.prompt}{hint}"

    def _single_question_visible(self) -> bool:
        request = self._state.pending_question_choice
        return request is not None and not request.multiple

    def _multiple_question_visible(self) -> bool:
        request = self._state.pending_question_choice
        return request is not None and request.multiple

    def _question_panel_height(self) -> int:
        request = self._state.pending_question_choice
        if request is None:
            return 0
        available = max(2, self._application.output.get_size().rows - 6)
        return min(len(request.choices) + 1, available)

    def _approval_panel_height(self) -> int:
        if self._state.pending_hitl is None or self._awaiting_denial_suggestion:
            return 0
        return min(
            len(self._approval_choices.values),
            max(1, self._application.output.get_size().rows - 6),
        )

    def _command_panel_height(self) -> int:
        request = self._state.pending_command_choice
        if request is None:
            return 0
        return min(
            len(request.choices) + 1,
            max(2, self._application.output.get_size().rows - 6),
        )

    def _completion_height(self) -> int:
        state = self._input.buffer.complete_state
        if state is None or self._has_pending_interaction():
            return 0
        available = self._application.output.get_size().rows - self._input_height() - 9
        return min(8, len(state.completions), max(0, available))

    # ── 键绑定 ──

    def _bindings(self) -> KeyBindings:
        bindings = KeyBindings()
        bindings.add("enter")(self._submit_key)
        try:
            bindings.add("s-enter")(self._insert_newline)
        except ValueError:
            pass
        steering = Condition(
            lambda: self._state.running and not self._has_pending_interaction()
        )
        bindings.add("escape", "enter", filter=steering)(self._steer_key)
        bindings.add("escape", "enter", filter=~steering)(self._insert_newline)
        bindings.add(
            "s-tab",
            eager=True,
            filter=Condition(
                lambda: (
                    not self._state.running
                    and not self._committing
                    and not self._has_pending_interaction()
                )
            ),
        )(self._cycle_mode_key)
        bindings.add("c-j")(self._insert_newline)
        bindings.add(Keys.PageUp, eager=True)(self._page_up_key)
        bindings.add(Keys.PageDown, eager=True)(self._page_down_key)
        bindings.add(Keys.End, eager=True)(self._end_key)
        bindings.add(Keys.ScrollUp)(self._scroll_up_key)
        bindings.add(Keys.ScrollDown)(self._scroll_down_key)
        bindings.add("?")(self._show_shortcuts_key)
        # 折叠快捷键必须是应用级绑定：焦点可能在授权列表等非输入控件。
        bindings.add("c-t", eager=True)(self._toggle_thinking_key)
        bindings.add("c-o", eager=True)(self._toggle_tools_key)
        bindings.add("c-q")(self._quit_key)
        bindings.add("c-c")(self._cancel_key)
        bindings.add("c-d")(self._eof_key)
        # 菜单或表单中的 Esc 立即取消；普通输入保留 Esc Enter 换行。
        bindings.add(
            "escape",
            eager=True,
            filter=Condition(
                lambda: (
                    self._state.pending_command_text is not None
                    or self._input.buffer.complete_state is not None
                )
            ),
        )(self._escape_key)
        return bindings

    def _cycle_mode_key(self, _event: object) -> None:
        """空闲时循环切换模式并保留尚未提交的输入。"""
        modes = ("act", "build", "plan")
        selected = modes[(modes.index(self._repl_state.mode) + 1) % len(modes)]
        command = f"/mode {selected}"
        self._record_command(command)
        self._run_command(command)

    def _submit_key(self, _event: object) -> None:
        buffer = self._input.buffer
        completion = buffer.complete_state
        if (
            completion is not None
            and completion.completions
            and self._completion_height() > 0
        ):
            buffer.go_to_completion(completion.complete_index or 0)
            buffer.complete_state = None
            self._refresh()
            return
        text = self._input.text.strip()
        if self._state.pending_command_text is not None:
            self._input.text = ""
            self._submit_command_text(text)
            return
        if self._awaiting_denial_suggestion:
            self._input.text = ""
            self._finish_denial(text)
            return
        if not text:
            return
        self._exit_pending = 0.0
        self._exit_pending_key = ""
        self._input.text = ""
        self._scrollback = 0
        if self._state.running or self._committing:
            if self._state.running and _is_live_command(text):
                self._record_command(text)
                self._run_command(text, preserve_running=True)
            elif self._state.running and not text.startswith(("/", "!", "$")):
                self._submit_busy_message(text)
            else:
                self._input.text = text
                self._state.log.append(
                    _LogEntry(
                        "system", "Run this command after the current task finishes."
                    )
                )
                self._refresh()
            return
        self._submit(text)

    def _steer_key(self, _event: object) -> None:
        """Alt Enter 将普通输入注入当前任务，不启动新的任务。"""
        text = self._input.text.strip()
        if not text:
            return
        if text.startswith(("/", "!", "$")):
            self._state.log.append(
                _LogEntry("system", "Type a message to steer the current task.")
            )
            self._refresh()
            return
        self._input.text = ""
        self._scrollback = 0
        self._submit_busy_message(text, BusyMessageMode.STEER)

    def _submit_busy_message(
        self, text: str, mode: BusyMessageMode | None = None
    ) -> None:
        """普通 Enter 使用排队策略，Alt Enter 显式引导当前任务。"""
        expanded_text, references = expand_file_references(text, self._project_root)
        outcome = self._agent_app.agent.submit_busy_message(
            UserMessage(content=expanded_text),
            mode or self._repl_state.busy_mode,
            display_text=text,
        )
        self._state.add_user(text)
        if references:
            self._store.append("event", file_reference_event(references))
        if outcome.status is SubmitStatus.STEER_ACCEPTED:
            self._state.log.append(
                _LogEntry("system", "Added guidance to the current task.")
            )
        elif outcome.status is SubmitStatus.FOLLOW_UP_QUEUED:
            self._state.log.append(
                _LogEntry(
                    "system",
                    "Queued for the next task.",
                )
            )
        elif outcome.status is SubmitStatus.INTERRUPT_REQUESTED:
            self._state.log.append(
                _LogEntry(
                    "system", "Stopping the current task; your message runs next."
                )
            )
        elif outcome.status is SubmitStatus.INJECT_QUEUED:
            self._state.log.append(
                _LogEntry("system", "The task is finishing; your guidance runs next.")
            )
        self._refresh()

    def _input_hint_text(self) -> str:
        if self._has_pending_interaction():
            text = "Enter confirm · Esc back · Ctrl+C cancel"
        elif self._completion_height():
            text = "↑/↓ choose · Enter accept · Esc close"
        elif self._state.running:
            action = {
                BusyMessageMode.FOLLOW_UP: "queue",
                BusyMessageMode.STEER: "steer",
                BusyMessageMode.INTERRUPT: "replace",
            }[self._repl_state.busy_mode]
            text = f"Enter {action} · Alt+Enter steer · Ctrl+J newline · Ctrl+C stop"
        else:
            text = "Enter send · Shift+Tab mode · Ctrl+J newline · @ files · / commands · cade -c resume"
        return fit_text(text, self._output_width())

    def _input_hint_height(self) -> int:
        """欢迎区已经说明空闲操作，底部只在交互状态变化时补充提示。"""
        return int(
            self._state.running
            or self._has_pending_interaction()
            or self._input.buffer.complete_state is not None
        )

    def _insert_newline(self, event: object) -> None:
        buffer = getattr(event, "current_buffer", None)
        if buffer is not None:
            buffer.insert_text("\n")

    def _page_up_key(self, _event: object) -> None:
        self._scroll_by(10)

    def _page_down_key(self, _event: object) -> None:
        self._scroll_by(-10)

    def _scroll_up_key(self, _event: object) -> None:
        self._scroll_by(3)

    def _scroll_down_key(self, _event: object) -> None:
        self._scroll_by(-3)

    def _end_key(self, _event: object) -> None:
        self._scrollback = 0
        self._refresh()

    def _show_shortcuts_key(self, event: object) -> None:
        if self._input.text:
            buffer = getattr(event, "current_buffer", None)
            if buffer is not None:
                buffer.insert_text("?")
            return
        self._state.log.append(_LogEntry("system", _SHORTCUT_HELP))
        self._scrollback = 0
        self._refresh()

    def _toggle_thinking_key(self, _event: object) -> None:
        self._update_preserving_viewport(self._state.toggle_thinking)
        self._repl_state.thinking_collapsed = self._state.thinking_collapsed
        self._refresh()

    def _toggle_tools_key(self, _event: object) -> None:
        self._update_preserving_viewport(self._state.toggle_tools)
        self._repl_state.tool_collapsed = self._state.tool_collapsed
        self._refresh()

    def _escape_key(self, _event: object) -> None:
        """Esc 关闭补全或文本表单，不改变待提交的输入。"""
        if self._state.pending_command_text is not None:
            self._cancel_pending_command_text()
            return
        if self._input.buffer.complete_state is not None:
            self._input.buffer.cancel_completion()
            self._refresh()
            return

    def _cancel_pending_command_text(self) -> None:
        """关闭文本表单并执行其取消回调。"""
        request = self._state.pending_command_text
        if request is None:
            return
        self._state.pending_command_text = None
        self._input.buffer.complete_state = None
        if request.on_cancel is not None:
            request.on_cancel()
        self._refresh()

    def _quit_key(self, _event: object) -> None:
        if self._state.pending_question_choice is not None:
            self._complete_question_choice([])
        if self._state.pending_command_text is not None:
            request = self._state.pending_command_text
            self._state.pending_command_text = None
            if request.on_cancel is not None:
                request.on_cancel()
        if self._state.pending_hitl is not None:
            self._finish_denial("")
        self._finish_exit()

    def _on_input_text_inserted(self, _buffer: object) -> None:
        """输入新内容即取消待确认的退出状态。"""
        self._input.buffer.complete_state = None
        self._exit_pending = 0.0
        self._exit_pending_key = ""

    def _on_input_layout_changed(self, _buffer: object) -> None:
        """输入和异步补全改变占用高度后同步重算历史视口。"""
        if hasattr(self, "_application"):
            self._refresh()

    def _cancel_key(self, _event: object) -> None:
        if self._state.pending_question_choice is not None:
            self._complete_question_choice([])
            return
        if self._state.pending_command_choice is not None:
            request = self._state.pending_command_choice
            self._state.pending_command_choice = None
            self._application.layout.focus(self._input)
            self._input.buffer.complete_state = None
            if request.on_cancel is not None:
                request.on_cancel()
            self._refresh()
            return
        if self._state.pending_command_text is not None:
            self._cancel_pending_command_text()
            return
        if self._state.pending_hitl is not None:
            self._finish_denial("")
        now = perf_counter()
        if exit_confirmed(
            self._exit_pending, self._exit_pending_key, now, ExitKeyKind.INTERRUPT
        ):
            self._finish_exit()
            return
        if self._input.text:
            self._input.text = ""
            self._arm_exit(now, ExitKeyKind.INTERRUPT)
            return
        if self._state.running:
            accepted = self._agent_app.agent.interrupt("interrupted by user")
            if accepted:
                self._store.append(
                    "event", {"type": "interrupted", "data": "interrupted by user"}
                )
                self._state.log.append(_LogEntry("stop", "[interrupt] stopping run"))
            self._refresh()
            return
        self._arm_exit(now, ExitKeyKind.INTERRUPT)

    def _eof_key(self, _event: object) -> None:
        """Ctrl+D：空闲且输入为空时参与双击退出，其余场景保持无操作。"""
        now = perf_counter()
        if exit_confirmed(
            self._exit_pending, self._exit_pending_key, now, ExitKeyKind.EOF
        ):
            self._finish_exit()
            return
        if self._state.running or self._input.text:
            return
        if self._has_pending_interaction():
            return
        self._arm_exit(now, ExitKeyKind.EOF)

    def _has_pending_interaction(self) -> bool:
        """是否存在需要用户先处理的菜单、表单或授权请求。"""
        return (
            self._awaiting_denial_suggestion
            or self._state.pending_hitl is not None
            or self._state.pending_command_choice is not None
            or self._state.pending_command_text is not None
            or self._state.pending_question_choice is not None
        )

    def _arm_exit(self, now: float, key: ExitKeyKind) -> None:
        """进入双击退出确认窗口，并提示再次按同一个键。"""
        self._exit_pending = now
        self._exit_pending_key = key.value
        self._state.log.append(_LogEntry("system", exit_confirm_hint(key)))
        self._refresh()

    def _finish_exit(self) -> None:
        """保存当前会话并退出 TUI。"""
        self._application.exit()

    # ── 提交 ──

    def _submit(self, text: str) -> None:
        skill_invocation = parse_skill_invocation(text)
        if skill_invocation is not None:
            skill_name, remaining_text = skill_invocation
            activation = activate_skill(
                self._agent_app,
                self._store,
                skill_name,
                mode=self._repl_state.mode,
            )
            self._state.log.append(_LogEntry("system", activation.message))
            if activation.status not in {"activated", "already_active"}:
                self._refresh()
                return
            if not remaining_text:
                self._refresh()
                return
            text = remaining_text
        if text.startswith("/"):
            self._record_command(text)
            self._run_command(text)
            self._refresh()
            return
        if text.startswith("!"):
            self._record_command(text)
            self._run_shell_shortcut(text)
            self._refresh()
            return

        expanded_text, references = expand_file_references(text, self._project_root)
        if references:
            self._store.append("event", file_reference_event(references))
        self._state.add_user(text)
        self._start_working()
        agent = getattr(self._agent_app, "agent", None)
        if agent is not None:
            token = getattr(agent, "cancellation_token", None)
            if token is not None:
                token.reset()
        self._refresh()
        thread = threading.Thread(
            target=self._run_turn,
            args=(expanded_text, text),
            daemon=True,
        )
        thread.start()

    def _run_command(self, text: str, *, preserve_running: bool = False) -> None:
        """执行斜杠命令；实时命令不得结束正在运行的 agent 回合。"""
        if text in {"/clear", "/new"}:
            self._clear_session()
            return
        if self._show_native_command_choice(text):
            return

        if not preserve_running:
            self._state.running = True

        def invoke(capture_output: bool) -> tuple[bool, str]:
            def handle() -> bool:
                return handle_command(
                    text,
                    self._store,
                    self._agent_app,
                    self._markdown_renderer,
                    self._repl_state,
                    self._prompt_session,
                    self._grant_store_manager.get_for_session(self._store.session_id)
                    if self._grant_store_manager is not None
                    else None,
                    self._permanent_grant_store,
                    static_policy=getattr(
                        self._agent_app.agent, "permission_policy", None
                    ),
                    restricted_dirs=getattr(
                        self._agent_app.agent, "restricted_dirs", ()
                    ),
                    snapshot_store=cast(SnapshotStore | None, self._snapshot_store),
                    show_session_history=True,
                )

            if not capture_output:
                return handle(), ""
            output = StringIO()
            with redirect_stdout(output):
                should_exit = handle()
            return should_exit, output.getvalue()

        async def run_inline() -> None:
            should_exit, output = await asyncio.get_running_loop().run_in_executor(
                None, invoke, True
            )
            if output.strip():
                self._state.log.append(_LogEntry("system", output.rstrip()))
            if preserve_running:
                self._refresh()
                self._submit_pending_input()
            else:
                self._finish_command(text, should_exit)

        if self._application.loop is None:
            asyncio.run(run_inline())
        else:
            self._application.create_background_task(run_inline())

    def _record_command(self, text: str) -> None:
        """记录用户输入的命令，命令本身不进入 agent 回合。"""
        self._store.append("event", {"type": "command", "data": text})
        self._state.add_command(text)

    def _show_native_command_choice(self, text: str) -> bool:
        """为需要选择的会话命令打开 TUI 原生菜单。"""
        command = text.split(maxsplit=1)[0]
        if command == "/mode" and len(text.split()) == 1:
            self._open_command_choices(
                [
                    (
                        f"{mode} (current)" if mode == self._repl_state.mode else mode,
                        mode,
                    )
                    for mode in ("act", "build", "plan")
                ],
                lambda selected: self._run_command(f"/mode {selected}"),
            )
            return True
        if command == "/model" and len(text.split()) == 1:
            self._open_model_selector()
            return True
        if command == "/permissions":
            self._open_command_choices(
                [
                    ("Show permission status", "/permissions list"),
                    ("Clear session permissions", "/permissions clear"),
                ],
                lambda selected: self._run_command(str(selected)),
            )
            return True
        if command == "/config":
            parts = text.split(maxsplit=2)
            query = parts[1].strip() if len(parts) > 1 else ""
            self._open_config_browser(query)
            return True
        if command == "/fork":
            entries = self._store.get_forkable_user_messages()
            if not entries:
                self._state.log.append(
                    _LogEntry("system", "No user messages to fork from.")
                )
                self._refresh()
                return True

            def fork(entry: object) -> None:
                parent_session_id = self._store.session_id
                entry_id = getattr(entry, "id", "")
                try:
                    forked = self._store.fork_from_entry(entry_id)
                except ValueError as exc:
                    self._state.log.append(_LogEntry("error", f"[error] {exc}"))
                    return
                self._store.current_path = forked.current_path
                meta = self._store.current_metadata()
                if self._snapshot_store is not None and meta is not None:
                    cast(SnapshotStore, self._snapshot_store).fork_session(
                        parent_session_id, meta.id
                    )
                self._agent_app.restore_session()
                self._state.restore_history(self._store.build_branch())
                self._sync_mode_from_agent()
                self._state.log.append(
                    _LogEntry("system", f'Forked at: "{meta.title if meta else ""}"')
                )

            self._open_command_choices(
                [(" ".join(str(e.content).split())[:100], e) for e in entries], fork
            )
            return True
        if command in {"/sessions", "/resume"} and len(text.split()) == 1:
            sessions = self._store.list_infos()
            if not sessions:
                self._state.log.append(_LogEntry("system", "No conversations found."))
                self._refresh()
                return True

            def resume(session: object) -> None:
                self._store.resume(getattr(session, "id", ""))
                self._agent_app.restore_session()
                self._restore_session_history()

            self._open_command_choices(
                [(f"{s.title} ({s.id[:8]})", s) for s in sessions], resume
            )
            return True
        if command == "/tree":
            nodes = self._store.get_tree()
            if not nodes:
                self._state.log.append(
                    _LogEntry("system", "No session tree available (no metadata).")
                )
                self._refresh()
                return True

            def jump(node: object) -> None:
                node_id = getattr(node, "id", "")
                if not self._store.jump_to_entry(node_id):
                    self._state.log.append(_LogEntry("error", "Failed to set entry."))
                    return
                self._agent_app.restore_session()
                self._state.restore_history(self._store.build_branch())
                self._sync_mode_from_agent()

            self._open_command_choices(
                [
                    (
                        (
                            f"{'  ' * n.depth}{'└─ ' if n.depth else ''}{n.title}"
                            f"{' ← current' if n.is_current else ''}"
                        ),
                        n,
                    )
                    for n in nodes
                ],
                jump,
            )
            return True
        return False

    def _open_model_selector(self) -> None:
        """在主 TUI 内选择模型，避免嵌套终端事件循环。"""
        from ..repl_settings import (
            AvailableModelEntry,
            _format_model_entry,
            get_available_model_entries,
            is_current_model_entry,
        )

        get_model_info = getattr(self._agent_app, "get_model_info", None)
        set_model = getattr(self._agent_app, "set_model", None)
        if not callable(get_model_info) or not callable(set_model):
            self._state.log.append(
                _LogEntry("system", "Model switching is not supported in this app.")
            )
            self._refresh()
            return

        raw_info = get_model_info()
        info = cast("dict[str, str]", raw_info) if isinstance(raw_info, dict) else {}
        current_model = str(info.get("model", ""))
        current_transport = str(info.get("transport", ""))
        available = get_available_model_entries(self._agent_app)
        if not available:
            self._state.log.append(
                _LogEntry(
                    "system",
                    "No available models with a signed-in account or configured API key were found.\n"
                    "Hint: run /login to sign in to ChatGPT, or configure an API key in the environment or .env file.",
                )
            )
            self._refresh()
            return

        def open_selector() -> None:
            choices: list[tuple[AnyFormattedText, object]] = []
            for entry in available:
                title = _format_model_entry(entry)
                if is_current_model_entry(entry, current_model, current_transport):
                    title = FormattedText([("class:model-current", title)])
                choices.append((title, entry))
            choices.append(("Enter a custom model name...", "__custom__"))
            self._open_command_choices(choices, choose)

        def switch(entry: AvailableModelEntry | None, model: str) -> None:
            try:
                new_model = set_model(
                    model=model,
                    transport=entry.transport if entry is not None else None,
                    profile="main",
                )
                raw_updated_info = get_model_info()
                updated_info = (
                    cast("dict[str, str]", raw_updated_info)
                    if isinstance(raw_updated_info, dict)
                    else {}
                )
                transport = str(
                    updated_info.get("transport")
                    or (entry.transport if entry is not None else "")
                )
                suffix = f" (transport: {transport})" if transport else ""
                self._state.log.append(
                    _LogEntry(
                        "system",
                        f"✓ Successfully switched to model: {new_model}{suffix}",
                    )
                )
            except (
                AttributeError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                self._state.log.append(
                    _LogEntry("error", f"Failed to switch model: {exc}")
                )

        def choose(selection: object) -> None:
            if selection == "__custom__":
                self._open_command_text(
                    "Enter the model name (esc to return)",
                    lambda value: (
                        switch(None, value.strip()) if value.strip() else None
                    ),
                    on_cancel=open_selector,
                )
                return
            entry = cast("AvailableModelEntry", selection)
            switch(entry, entry.model)

        open_selector()

    def _command_choice_hint_text(self) -> str:
        """选择菜单底部的灰色说明：跟随高亮项，否则显示操作提示。"""
        request = self._state.pending_command_choice
        if request is None:
            return ""
        if request.describe is not None:
            description = request.describe(self._command_choices.current_value)
            if description:
                return f"\n  {description}"
        return "\n  ↑/↓ move · enter select · esc back/cancel"

    def _open_command_choices(
        self,
        choices: Sequence[tuple[AnyFormattedText, object]],
        on_select: Callable[[object], None],
        on_cancel: Callable[[], None] | None = None,
        describe: Callable[[object], str] | None = None,
    ) -> None:
        """打开可复用的 TUI 命令选择菜单。

        on_cancel 提供时，esc 触发它而不是直接关闭（用于二级菜单返回上级）；
        describe 提供时，底部灰色说明跟随高亮项。
        """
        stored_choices = list(choices)
        self._state.pending_command_choice = _CommandChoiceRequest(
            stored_choices, on_select, on_cancel, describe
        )
        self._command_choices.values = [
            (value, label) for label, value in stored_choices
        ]
        self._command_choices._selected_index = 0
        self._command_choices.current_value = stored_choices[0][1]
        self._application.layout.focus(self._command_choices)
        self._refresh()

    def _open_command_text(
        self,
        prompt: str,
        on_submit: Callable[[str], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        """打开单行文本表单。"""
        self._state.pending_command_text = _CommandTextRequest(
            prompt, on_submit, on_cancel
        )
        self._application.layout.focus(self._input)
        self._refresh()

    def _submit_command_text(self, text: str) -> None:
        request = self._state.pending_command_text
        if request is None:
            return
        self._state.pending_command_text = None
        request.on_submit(text)
        self._refresh()

    def _open_config_browser(self, query: str = "") -> None:
        """在 TUI 内打开设置浏览器：选择行进入编辑流程。"""
        from ..config_registry import (
            SETTING_SPECS,
            find_setting,
            format_setting,
            load_effective_config,
            matching_settings,
        )
        from ..setup_wizard import CONFIG_FILENAME

        config_path = self._project_root / CONFIG_FILENAME
        config = load_effective_config(config_path)

        def choose(selection: object) -> None:
            if selection is None:
                return
            self._edit_config_setting(config_path, cast("SettingSpec", selection))

        if query:
            spec = find_setting(query)
            if spec is None:
                matches = matching_settings(query)
                note = (
                    f"'{query}' is ambiguous: "
                    + ", ".join(match.label for match in matches)
                    if matches
                    else f"No setting matches '{query}'."
                )
                self._state.log.append(_LogEntry("system", note))
                self._refresh()
                return
            choose(spec)
            return

        choices: list[tuple[str, object]] = [
            (f"{item.label:<28}{format_setting(item, config)}", item)
            for item in SETTING_SPECS
        ]
        choices.append(("Exit", None))

        def describe(item: object) -> str:
            if isinstance(item, SettingSpec):
                return f"{item.key} — {item.description}"
            return ""

        self._open_command_choices(choices, choose, describe=describe)

    def _edit_config_setting(self, config_path: Path, spec: SettingSpec) -> None:
        """编辑单个设置项：枚举走选择菜单，标量走文本输入。"""
        from ..config_registry import (
            SettingKind,
            format_setting,
            load_effective_config,
            save_setting_text,
            setting_detail,
        )

        config = load_effective_config(config_path)
        current = format_setting(spec, config)

        def reopen() -> None:
            self._open_config_browser()

        def report_and_reopen(ok: bool, message: str) -> None:
            level = "system" if ok else "error"
            self._state.log.append(_LogEntry(level, message))
            reopen()

        if spec.kind is SettingKind.INFO:
            lines = [f"{spec.label} ({current}):"]
            if spec.description:
                lines.append(f"  {spec.description}")
            lines.extend(setting_detail(spec, config))
            for line in lines:
                self._state.log.append(_LogEntry("system", line))
            reopen()
            return

        if spec.kind in (SettingKind.BOOL, SettingKind.ENUM):
            tokens = ("on", "off") if spec.kind is SettingKind.BOOL else spec.choices
            titles = [
                token + " (current)" if token == current.lower() else token
                for token in tokens
            ]

            def pick(token: object) -> None:
                text = str(token).removesuffix(" (current)")
                ok, message = save_setting_text(config_path, spec, text)
                report_and_reopen(ok, message)

            def describe_token(token: object) -> str:
                text = str(token).removesuffix(" (current)")
                return spec.describe_choice(text)

            self._open_command_choices(
                [(title, title) for title in titles],
                pick,
                on_cancel=reopen,
                describe=describe_token,
            )
            return

        hint = "Type value, enter to save, esc to cancel"
        if spec.nullable:
            hint += "; 'none' clears"

        def submit(value: str) -> None:
            ok, message = save_setting_text(config_path, spec, value)
            report_and_reopen(ok, message)

        self._open_command_text(
            f"{spec.key} = {current} — {hint}", submit, on_cancel=reopen
        )

    def _accept_command_choice(self) -> None:
        request = self._state.pending_command_choice
        if request is None:
            return
        selected = self._command_choices.current_value
        self._state.pending_command_choice = None
        # 先恢复输入框焦点，再执行回调：链式打开的下一级菜单或文本表单
        # 会在回调内重新聚焦，不能被这里的默认焦点覆盖。
        self._application.layout.focus(self._input)
        self._scrollback = 0
        request.on_select(selected)
        self._refresh()

    def _finish_command(self, text: str, should_exit: bool) -> None:
        """同步命令执行后的 TUI 状态。"""
        self._state.mode = self._repl_state.mode
        if _is_session_history_command(text):
            self._restore_session_history()
        self._state.running = False
        self._refresh()
        if should_exit:
            self._application.exit()
        self._submit_pending_input()

    def _clear_session(self) -> None:
        """在 inline TUI 内创建空会话，不切换到终端清屏输出。"""
        self._store.clear()
        self._agent_app.restore_session()
        self._state.restore_history([])
        self._state.log.append(_LogEntry("welcome", self._welcome_text()))
        self._scrollback = 0
        agent = getattr(self._agent_app, "agent", None)
        if agent is not None:
            agent.session_id = self._store.session_id
            from ..repl_commands import _compute_context_summary

            _compute_context_summary(agent, self._project_root, self._repl_state)
        self._refresh()

    def _run_shell_shortcut(self, text: str) -> None:
        """在后台执行 shell 快捷命令，保持 TUI 事件循环可响应。"""
        agent = getattr(self._agent_app, "agent", None)
        if agent is not None:
            token = getattr(agent, "cancellation_token", None)
            if token is not None:
                token.reset()
        self._state.running = True
        self._refresh()

        def run() -> None:
            try:
                output = run_shell_shortcut(text, self._agent_app)
                self._store.append("event", {"type": "shell_shortcut", "data": text})
                self._store.append("event", {"type": "tool_result", "data": output})
                self._state.log.append(_LogEntry("shell", output, markdown=False))
            except (LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
                detail = str(exc) or repr(exc)
                self._state.log.append(
                    _LogEntry("error", f"[error] {type(exc).__name__}: {detail}")
                )
            finally:
                self._state.running = False
                self._call_in_ui_thread(self._schedule_turn_commit)

        threading.Thread(target=run, daemon=True).start()

    def _restore_session_history(self) -> None:
        """恢复会话命令完成后，以 TUI 形式重新渲染当前分支。"""
        self._state.restore_history(self._store.build_branch())
        self._scrollback = 0
        agent = getattr(self._agent_app, "agent", None)
        if agent is not None:
            agent.session_id = self._store.session_id
        self._sync_mode_from_agent()

    def _sync_mode_from_agent(self) -> None:
        """让 TUI 与 REPL 状态中的模式与 agent harness 保持一致。

        新会话取配置的默认模式，恢复会话取 transcript 中持久化的模式。
        """
        agent = getattr(self._agent_app, "agent", None)
        mode = getattr(agent, "current_mode", None)
        if mode in {"plan", "build", "act"}:
            self._repl_state.mode = mode
            self._state.mode = mode

    def _restore_startup_session(
        self,
        resume_latest: bool,
        auto_continue: bool,
        session_id: str | None,
    ) -> None:
        """在 TUI 创建前选择并恢复会话，避免以空白会话覆盖历史。"""
        selected = None
        if session_id is not None:
            selected = self._store.find_by_id(session_id)
            if selected is None:
                raise ValueError(f"Session not found: {session_id}")
            stored = (
                Path(selected.project_path).resolve() if selected.project_path else None
            )
            if stored is None or stored != self._project_root.resolve():
                raise ValueError(
                    f"Session belongs to another project: {selected.project_path}"
                )
        elif auto_continue:
            selected = self._store.find_latest_for_project(self._project_root)
            if selected is None:
                self._startup_notice = (
                    "No previous task in this project. Start a new task below."
                )
        elif resume_latest:
            selected = select_session_interactively(
                self._store.list_infos(), "Select session to resume:"
            )
        if selected is None:
            return
        self._store.resume(selected.id)
        self._agent_app.restore_session()
        self._state.restore_history(self._store.build_branch())
        self._startup_notice = (
            f"Resumed {selected.title} ({selected.id}). Continue your task below."
        )

    # ── HITL ──

    def _question_prompt_callback(
        self, questions: list[dict[str, object]]
    ) -> list[list[str]]:
        """在现有 TUI application 内依次收集 question 工具回答。"""
        answers: list[list[str]] = []
        for question in questions:
            options = question.get("options")
            if isinstance(options, list) and options:
                choices = [
                    (
                        _question_choice_text(option),
                        str(option.get("label", "")),
                    )
                    for option in options
                    if isinstance(option, dict)
                ]
                # 追加「自定义输入」选项
                choices.append((CUSTOM_OPTION_LABEL, CUSTOM_OPTION_LABEL))

                result = self._wait_for_question_choice(
                    str(question.get("question", "")),
                    choices,
                    bool(question.get("multiple", False)),
                )

                multiple = bool(question.get("multiple", False))
                if multiple:
                    if CUSTOM_OPTION_LABEL in result:
                        custom = self._wait_for_question_text(
                            str(question.get("question", ""))
                        )
                        answers.append(custom if custom else [])
                    else:
                        answers.append(result)
                else:
                    if result and result[0] == CUSTOM_OPTION_LABEL:
                        custom = self._wait_for_question_text(
                            str(question.get("question", ""))
                        )
                        answers.append(custom if custom else [])
                    else:
                        answers.append(result)
                continue

            answers.append(
                self._wait_for_question_text(str(question.get("question", "")))
            )
        return answers

    def _wait_for_question_text(self, prompt: str) -> list[str]:
        """打开文本输入并等待用户提交回答。"""
        event = Event()
        answer: list[str] = []

        def submit(value: str) -> None:
            if value:
                answer.append(value)
            event.set()

        def show_text() -> None:
            self._open_command_text(prompt, submit, event.set)

        self._call_in_ui_thread(show_text)
        event.wait()
        return answer

    def _wait_for_question_choice(
        self,
        prompt: str,
        choices: list[tuple[str, str]],
        multiple: bool,
    ) -> list[str]:
        request = _QuestionChoiceRequest(prompt, choices, multiple, Event())

        def show_choices() -> None:
            self._state.pending_question_choice = request
            values = [(value, label) for label, value in choices]
            if multiple:
                self._question_checkboxes.values = values
                self._question_checkboxes._selected_index = 0
                self._question_checkboxes.current_values = []
                self._application.layout.focus(self._question_checkboxes)
            else:
                self._question_choices.values = values
                self._question_choices._selected_index = 0
                self._question_choices.current_value = choices[0][1]
                self._application.layout.focus(self._question_choices)
            self._refresh()

        self._call_in_ui_thread(show_choices)
        request.event.wait()
        return request.result

    def _call_in_ui_thread(self, callback: Callable[[], None]) -> None:
        loop = self._application.loop
        if loop is None:
            callback()
        else:
            loop.call_soon_threadsafe(callback)

    def _accept_question_choice(self) -> None:
        request = self._state.pending_question_choice
        if request is None:
            return
        if request.multiple:
            result = list(self._question_checkboxes.current_values)
        else:
            result = [self._question_choices.current_value]
        self._complete_question_choice(result)

    def _complete_question_choice(self, result: list[str]) -> None:
        request = self._state.pending_question_choice
        if request is None:
            return
        request.result = result
        self._state.pending_question_choice = None
        request.event.set()
        self._application.layout.focus(self._input)
        self._scrollback = 0
        self._refresh()

    def _approval_callback(self, approval: ApprovalRequest) -> HITLResult:
        from cade.harness.security import HITLResult

        request = _HitlRequest(
            tool_name=approval.tool.name,
            preview=[
                _strip_rich_markup(line)
                for line in (
                    tool_preview_lines(approval.tool, approval.action_input)
                    + approval_scope_lines(
                        approval.tool,
                        approval.action_input,
                        approval.allowed_scopes,
                    )
                )
            ],
            event=Event(),
        )
        self._state.pending_hitl = request

        def show_choices() -> None:
            choices = hitl_choices(approval.allowed_scopes)
            self._approval_choices.values = [(choice, choice) for choice in choices]
            self._approval_choices._selected_index = 0
            self._approval_choices.current_value = choices[0]
            self._approval_choices.current_values = [choices[0]]
            self._application.layout.focus(self._approval_choices)
            self._refresh()

        loop = self._application.loop
        if loop is None:
            show_choices()
        else:
            loop.call_soon_threadsafe(show_choices)
        request.event.wait()
        result = request.result or HITLResult("deny", "once")
        if self._state.pending_hitl is request:
            self._state.pending_hitl = None
        return result

    def _submit_pending_input(self) -> None:
        """宿主只根据 durable inbox 的 wake 状态启动运行。"""
        if self._state.running or not self._agent_app.agent.has_pending_input():
            return
        self._start_working()
        threading.Thread(
            target=self._run_turn,
            args=(None, None),
            daemon=True,
        ).start()

    def _accept_approval_choice(self) -> None:
        choice = self._approval_choices.current_value
        if choice == "Deny":
            self._awaiting_denial_suggestion = True
            self._application.layout.focus(self._input)
            self._refresh()
            return
        result = parse_hitl_choice(choice)
        if result is not None:
            self._complete_approval(result)

    def _finish_denial(self, suggestion: str) -> None:
        from cade.harness.security import HITLResult

        self._complete_approval(HITLResult("deny", "once", suggestion=suggestion))

    def _complete_approval(self, result: HITLResult) -> None:
        request = self._state.pending_hitl
        if request is None:
            return
        request.result = result
        self._state.pending_hitl = None
        self._awaiting_denial_suggestion = False
        request.event.set()
        self._application.layout.focus(self._input)
        self._refresh()

    # ── Turn 执行 ──

    def _run_turn(self, text: str | None, display_text: str | None) -> None:
        snapshot = self._snapshot_store
        _snapshot_ctx = _enter_snapshot_ctx(snapshot, self._store.session_id)
        turn_log_start = len(self._state.log)

        answer = ""
        tool_names: list[str] = []
        try:
            self._last_stream_refresh = 0.0
            for event in self._agent_app.ask_stream(
                text,
                mode=self._repl_state.mode,
                display_question=display_text,
            ):
                if isinstance(event, (FinalStructuredEvent,)):
                    answer = event.data.answer
                if isinstance(event, (ToolUseStructuredEvent,)):
                    tool_names.append(event.data.name)
                self._dispatch_agent_event(event)
        except (LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._wait_for_agent_events()
            self._save_partial_answer(turn_log_start)
            detail = str(exc) or repr(exc)
            self._state.log.append(
                _LogEntry("error", f"[error] {type(exc).__name__}: {detail}")
            )
            self._state.running = False
            self._stop_working()
            self._schedule_turn_commit()
            return

        self._wait_for_agent_events()
        self._stop_working()

        if not answer:
            self._save_partial_answer(turn_log_start)

        self._refresh()

        _exit_snapshot_ctx(snapshot, _snapshot_ctx, self._store.session_id, tool_names)
        from ..repl_commands import _compute_context_summary

        _compute_context_summary(
            self._agent_app.agent, self._project_root, self._repl_state
        )
        self._schedule_turn_commit()

    def _schedule_turn_commit(self) -> None:
        """保留回合内容，允许完成后继续折叠和滚动查看。"""
        self._committing = False
        self._refresh()
        self._submit_pending_input()
        self._refresh_workspace_branch()

    def _refresh_workspace_branch(self) -> None:
        """回合结束后在后台读取分支，避免渲染时阻塞终端。"""

        def update() -> None:
            branch = git_branch_name(self._project_root)

            def apply() -> None:
                self._workspace_branch = branch
                self._refresh()

            self._call_in_ui_thread(apply)

        threading.Thread(target=update, daemon=True).start()

    def _save_partial_answer(self, turn_log_start: int) -> None:
        """将中断前已经流式显示的回答写入会话，供恢复和后续注入使用。"""
        partial = "".join(
            entry.content()
            for entry in self._state.log[turn_log_start:]
            if entry.role == "cade"
        ).strip()
        if self._state.streaming_answer is not None:
            partial = (partial + self._state.streaming_answer.content()).strip()
        if partial:
            recorder = getattr(self._agent_app, "session_recorder", None)
            if recorder is None:
                raise RuntimeError("session recorder is not configured")
            recorder.record_assistant(partial)

    def _dispatch_agent_event(self, event: AgentHarnessEvent) -> None:
        """将 agent 事件放入队列，由 UI loop 批量更新显示状态。"""
        loop = self._application.loop
        if loop is None:
            self._state.handle_event(event)
            self._refresh_streaming()
            return

        self._agent_event_queue.put(event)
        with self._agent_event_queue_lock:
            if self._agent_event_drain_scheduled:
                return
            self._agent_event_drain_scheduled = True
        loop.call_soon_threadsafe(self._drain_agent_events)

    def _drain_agent_events(self) -> None:
        """在 UI 线程批量处理已排队事件，并最多触发一次重绘。"""
        with self._agent_event_drain_lock:
            while True:
                try:
                    event = self._agent_event_queue.get_nowait()
                except Empty:
                    break
                self._state.handle_event(event)
        self._refresh_streaming()
        with self._agent_event_queue_lock:
            self._agent_event_drain_scheduled = False
            has_pending = not self._agent_event_queue.empty()
            if has_pending:
                self._agent_event_drain_scheduled = True
        if has_pending:
            loop = self._application.loop
            if loop is not None:
                loop.call_soon(self._drain_agent_events)

    def _wait_for_agent_events(self) -> None:
        """等待本回合已产生的事件全部进入状态，避免保存结果时竞态。"""
        loop = self._application.loop
        if loop is None:
            return
        if loop.is_closed() or not loop.is_running():
            self._drain_agent_events()
            return
        completed = Event()

        def finish() -> None:
            self._drain_agent_events()
            completed.set()

        try:
            loop.call_soon_threadsafe(finish)
        except RuntimeError:
            self._drain_agent_events()
            return
        if not completed.wait(self._EVENT_DRAIN_TIMEOUT):
            self._drain_agent_events()

    # ── 刷新 ──

    def _fragments(self) -> StyleAndTextTuples:
        """只绘制实际内容，长对话使用受终端高度限制的滚动视口。"""
        height = self._output_height()
        width = self._output_width()
        count = self._state.line_count(width)
        fragments = self._state.fragments(height, self._scrollback, width)
        if count >= height and fragments and fragments[-1] == ("", "\n"):
            fragments.pop()
        return fragments

    def _output_width(self) -> int:
        return max(1, self._application.output.get_size().columns)

    def _output_height(self) -> int:
        approval_height = self._approval_panel_height()
        input_visible = (
            self._state.pending_command_choice is None
            and (self._state.pending_question_choice is None)
            and (self._state.pending_hitl is None or self._awaiting_denial_suggestion)
        )
        input_area_height = self._input_height() + 2 if input_visible else 0
        command_height = self._command_panel_height()
        question_height = self._question_panel_height()
        available = max(
            1,
            self._application.output.get_size().rows
            - 1
            - input_area_height
            - 2
            - self._input_hint_height()
            - approval_height
            - command_height
            - question_height
            - self._completion_height(),
        )
        return min(available, self._state.line_count(self._output_width()) + 1)

    def _input_height(self) -> int:
        # 输入内容可能没有换行符，但会因终端宽度产生视觉折行。
        # 按显示宽度计算高度，避免视口只露出光标所在的最后一段。
        columns = max(1, self._application.output.get_size().columns - 1)
        prompt_width = fragment_list_width(self._input_prompt())
        visual_lines = 0
        for index, line in enumerate(self._input.text.split("\n")):
            line_width = get_cwidth(line)
            if index == 0:
                line_width += prompt_width
            quotient, remainder = divmod(line_width, columns)
            visual_lines += max(1, quotient + bool(remainder))
        return min(5, visual_lines)

    def _max_scrollback(self) -> int:
        return max(
            0, self._state.line_count(self._output_width()) - self._output_height()
        )

    def _scroll_by(self, amount: int) -> None:
        self._scrollback = max(
            0, min(self._max_scrollback(), self._scrollback + amount)
        )
        self._refresh()

    def _update_preserving_viewport(self, update: Callable[[], None]) -> None:
        """更新会改变行数的显示状态，并保持当前视口的顶部位置。"""
        if self._scrollback == 0:
            update()
            return
        top_line = max(
            0,
            self._state.line_count(self._output_width())
            - self._output_height()
            - self._scrollback,
        )
        update()
        self._scrollback = max(
            0,
            self._state.line_count(self._output_width())
            - self._output_height()
            - top_line,
        )

    def _start_working(self) -> None:
        """启动独立的工作状态动画，不把它当作 reasoning。"""
        self._state.start_working()
        self._schedule_working_refresh()
        self._refresh()

    def _stop_working(self) -> None:
        """停止工作状态动画；reasoning 内容由输出状态单独管理。"""
        self._state.stop_working()
        if self._working_refresh_handle is not None:
            self._working_refresh_handle.cancel()
            self._working_refresh_handle = None
        self._refresh()

    def _schedule_working_refresh(self) -> None:
        loop = self._application.loop
        if (
            not self._state.working
            or loop is None
            or not loop.is_running()
            or self._working_refresh_handle is not None
        ):
            return
        self._working_refresh_handle = loop.call_later(0.1, self._refresh_working)

    def _refresh_working(self) -> None:
        """刷新工作动画并安排下一帧。"""
        self._working_refresh_handle = None
        if not self._state.working:
            return
        self._refresh()
        self._schedule_working_refresh()

    def _refresh(self) -> None:
        self._prepare_frame()
        self._application.invalidate()

    def _prepare_frame(self) -> None:
        """在绘制前按当前尺寸重算视口，缩放后仍跟随最新输出。"""
        if self._state.log and self._state.log[0].role == "welcome":
            self._state.log[0].text = self._welcome_text()
        self._scrollback = min(self._scrollback, self._max_scrollback())
        self._output_control.text = self._fragments()

    def _refresh_streaming(self) -> None:
        """限制流式输出重绘频率，避免每个 delta 都触发完整布局。"""
        now = perf_counter()
        elapsed = now - self._last_stream_refresh
        if elapsed < self._STREAM_REFRESH_INTERVAL:
            loop = self._application.loop
            if (
                loop is not None
                and loop.is_running()
                and self._stream_refresh_handle is None
            ):
                self._stream_refresh_handle = loop.call_later(
                    self._STREAM_REFRESH_INTERVAL - elapsed,
                    self._flush_stream_refresh,
                )
            return
        if self._stream_refresh_handle is not None:
            self._stream_refresh_handle.cancel()
            self._stream_refresh_handle = None
        self._last_stream_refresh = now
        self._refresh()

    def _flush_stream_refresh(self) -> None:
        """补发节流窗口末尾的最后一次流式重绘。"""
        self._stream_refresh_handle = None
        self._last_stream_refresh = perf_counter()
        self._refresh()

    def _status_text(self) -> FormattedText:
        width = self._application.output.get_size().columns
        project = compact_path(self._project_root)
        if self._workspace_branch:
            project += f" ({self._workspace_branch})"
        context = (
            f"context: {self._repl_state.context_usage}"
            if self._repl_state.context_usage
            else ""
        )
        if self._repl_state.usage_stats and width >= 90:
            context += f"  {self._repl_state.usage_stats}"
        first = status_line(
            project,
            context,
            width,
            keep_left_end=True,
            left_style="class:status-accent",
        )
        if self._workspace_branch:
            path, separator, branch = first[0][1].rpartition(" (")
            if separator:
                first[:1] = [
                    ("class:status-accent", path),
                    ("class:status", separator + branch),
                ]
        mode = f"● {self._repl_state.mode}"
        if self._state.working:
            mode = f"{working_status_text()}  {mode}"
        elif self._scrollback:
            mode += "  ↑ history · End latest"
        elif self._state.pending_hitl is not None:
            mode = "Awaiting approval"
        second = status_line(
            mode,
            self._model_status(),
            width,
            left_style="class:status-mode",
            right_style="class:status",
        )
        return FormattedText([*first, ("", "\n"), *second])

    def _model_status(self) -> str:
        get_model_info = getattr(self._agent_app, "get_model_info", None)
        raw_info = get_model_info() if callable(get_model_info) else None
        info = raw_info if isinstance(raw_info, dict) else {}
        model = str(info.get("model") or self._repl_state.model_name or "unknown")
        effort = str(info.get("reasoning_effort") or "")
        return f"{model} · {effort}" if effort else model

    def _welcome_text(self) -> str:
        return welcome_text(
            version=self._welcome_version,
            tools=self._welcome_tools,
            instructions=self._welcome_instructions,
            skills=self._welcome_skills,
            width=self._output_width(),
            compact=self._application.output.get_size().rows < 28,
        )


# ── 模块级工具函数 ──


def _is_live_command(text: str) -> bool:
    """判断命令是否可以在 agent 回合执行期间提交。"""
    return text.split(maxsplit=1)[0] in {"/steer", "/queue", "/goal"}


def _tui_history(project_root: Path) -> History | None:
    """为 TUI 输入栏创建与 REPL 相同的持久化历史记录。"""
    try:
        from prompt_toolkit.history import FileHistory

        history_dir = project_root / ".cade"
        history_dir.mkdir(parents=True, exist_ok=True)
        return FileHistory(str(history_dir / "repl_history"))
    except OSError:
        return None


def _strip_rich_markup(text: str) -> str:
    """Rich 标记不能由 prompt_toolkit 解析，转换为保留内容的纯文本。"""
    return re.sub(r"\[/?[^\]]+]", "", text)


def _question_choice_text(option: dict[str, object]) -> str:
    """组合 question 选项标签和说明。"""
    label = str(option.get("label", ""))
    description = option.get("description")
    if isinstance(description, str) and description.strip():
        return f"{label} - {description.strip()}"
    return label


def _is_session_history_command(command: str) -> bool:
    """判断命令是否会切换或重写当前会话分支。"""
    name = command.split(maxsplit=1)[0]
    return name in {"/resume", "/continue", "/sessions", "/tree", "/rewind"}


def _init_snapshot_store(project_root: Path) -> object | None:
    try:
        return SnapshotStore(project_root)
    except SnapshotUnsupportedError:
        return None


def _enter_snapshot_ctx(
    snapshot_store: object | None, session_id: str
) -> tuple[str, SnapshotService, SnapshotResult] | None:
    if snapshot_store is None:
        return None
    try:
        from typing import cast as _cast

        from cade.harness.snapshot import SnapshotStore as _S

        store = _cast(_S, snapshot_store)
        turn_id = store.next_turn_id(session_id)
        service = store.service(session_id)
        pre_result = service.track()
        return (turn_id, service, pre_result)
    except (LookupError, OSError, RuntimeError, TypeError, ValueError):
        return None


def _exit_snapshot_ctx(
    snapshot_store: object | None,
    ctx: tuple[str, SnapshotService, SnapshotResult] | None,
    session_id: str,
    tool_names: list[str],
) -> None:
    if snapshot_store is None or ctx is None:
        return
    try:
        from typing import cast as _cast

        from cade.harness.snapshot import SnapshotStore as _S

        store = _cast(_S, snapshot_store)
        turn_id, service, pre_result = ctx
        post_result = service.track()
        changes = service.diff(pre_result.snapshot_id, post_result.snapshot_id)
        skipped_files = [
            *pre_result.skipped_files,
            *post_result.skipped_files,
        ]
        store.record_turn(
            session_id=session_id,
            turn_id=turn_id,
            pre_snapshot_id=pre_result.snapshot_id,
            post_snapshot_id=post_result.snapshot_id,
            changed_files=changes,
            skipped_files=skipped_files,
            tool_names=tool_names,
        )
    except (LookupError, OSError, RuntimeError, TypeError, ValueError):
        logger.debug("failed to record snapshot turn", exc_info=True)
