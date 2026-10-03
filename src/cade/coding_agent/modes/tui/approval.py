from __future__ import annotations

from cade.agent.types import ApprovalScope, ToolInput, ToolSpec
from cade.harness.security.permission_model.utils import command_grant_pattern

from .tool_summary import brief_input


def tool_preview_lines(tool: ToolSpec, action_input: ToolInput) -> list[str]:
    """生成 TUI 授权预览内容。"""
    lines: list[str] = []

    # Tool name
    lines.append(f"[bold]Tool:[/bold] {tool.name}")

    # ToolInput is always dict[str, Any]
    if tool.name == "edit":
        _preview_edit_file(action_input, lines)
    elif tool.name == "bash":
        _preview_bash(action_input, lines)
    elif tool.name == "write":
        _preview_write_file(action_input, lines)
    elif tool.name == "read":
        path = action_input.get("path", "")
        lines.append(f"[bold]File:[/bold] {path}")
    elif tool.name in ("grep", "glob", "find"):
        _preview_search(tool.name, action_input, lines)
    else:
        brief = brief_input(tool.name, action_input)
        lines.append(f"[bold]Input:[/bold] {brief}")

    return lines


def approval_scope_lines(
    tool: ToolSpec,
    action_input: ToolInput,
    allowed_scopes: tuple[ApprovalScope, ...],
) -> list[str]:
    """说明 session/permanent 选项实际会授予的范围。"""
    if not {"session", "permanent"}.intersection(allowed_scopes):
        return []

    if tool.name == "bash":
        command = action_input.get("command") or action_input.get("input", "")
        subject = (
            f"bash commands matching [bold]{command_grant_pattern(command)}[/bold]"
            if command
            else "this bash action"
        )
    else:
        subject = f"this [bold]{tool.name}[/bold] action"

    lines: list[str] = []
    if "session" in allowed_scopes:
        lines.append(f"[dim]Session allow: {subject} until this session ends.[/dim]")
    if "permanent" in allowed_scopes:
        lines.append(
            "[yellow]Always allow: save this rule for this project in "
            ".cade/approval_grants.json.[/yellow]"
        )
    return lines


def _preview_edit_file(action_input: dict, lines: list[str]) -> None:
    """为 edit_file 工具显示文件路径和 mini diff。"""
    path = action_input.get("path", "")
    old_text = action_input.get("old_text", "")
    new_text = action_input.get("new_text", "")
    replace_all = action_input.get("replace_all", False)

    lines.append(f"[bold]File:[/bold] {path}")
    if replace_all:
        lines.append("[yellow]Replace all occurrences[/yellow]")

    if old_text or new_text:
        # Show compact diff snippet
        old_snippet = old_text[:280].replace("\n", "¶ ")
        new_snippet = new_text[:280].replace("\n", "¶ ")
        if old_snippet:
            lines.append(f"[red]─ {old_snippet}[/red]")
        if new_snippet:
            lines.append(f"[green]+ {new_snippet}[/green]")

        if len(old_text) > 280 or len(new_text) > 280:
            lines.append("[dim]  (truncated, see above for full)[/dim]")


def _preview_bash(action_input: dict, lines: list[str]) -> None:
    """为 bash 工具显示命令和结构化分析。"""
    command = action_input.get("command") or action_input.get("input", "")
    if not command:
        lines.append("[bold]Command:[/bold] (empty)")
        return

    lines.append(f"[bold]Command:[/bold] {command[:500]}")

    parts = command.strip().split()
    if not parts:
        return

    cmd = parts[0].lower()

    # 危险 flag 检测
    dangerous_flags = {"--force", "-f", "--hard", "--destroy", "--delete"}
    flags = {p for p in parts[1:] if p.startswith("-")}
    matched_danger = flags & dangerous_flags
    if matched_danger:
        lines.append(f"[red]⚠  Danger flags: {' '.join(matched_danger)}[/red]")

    # 命令解释
    interpretations = {
        "rm": "Remove files/directories",
        "cp": "Copy files",
        "mv": "Move/rename files",
        "git": "Git operation",
        "docker": "Docker container operation",
        "pip": "Python package management",
        "npm": "Node package management",
        "curl": "HTTP request",
        "wget": "Download file",
        "chmod": "Change file permissions",
        "chown": "Change file owner",
        "mkdir": "Create directory",
        "touch": "Create file / update timestamp",
        "cat": "Display file contents",
        "echo": "Output text",
        "grep": "Search text patterns",
        "find": "Search files",
        "sed": "Stream editor (file modification)",
        "awk": "Text processing",
        "sort": "Sort lines",
        "head": "Show first lines of file",
        "tail": "Show last lines of file",
        "wc": "Count lines/words/bytes",
        "ls": "List directory contents",
        "ps": "Show process status",
        "kill": "Terminate a process",
        "systemctl": "Systemd service management",
        "apt": "APT package manager (Debian/Ubuntu)",
        "yum": "YUM package manager (RHEL/CentOS)",
        "brew": "Homebrew package manager (macOS)",
        "python": "Run Python interpreter/script",
        "python3": "Run Python 3 interpreter/script",
        "node": "Run Node.js script",
        "npx": "Execute Node package",
        "deno": "Run Deno script",
        "cargo": "Rust package manager",
        "go": "Go toolchain",
        "rustc": "Rust compiler",
        "dotnet": ".NET CLI",
        " terraform": "Terraform infrastructure",
        "kubectl": "Kubernetes CLI",
        "helm": "Kubernetes package manager",
        "ssh": "SSH remote connection",
        "scp": "Secure copy over SSH",
        "rsync": "Remote file sync",
        "make": "Build automation",
        "cmake": "CMake build system",
        "gradle": "Gradle build tool",
        "mvn": "Maven build tool",
    }
    if cmd in interpretations:
        lines.append(f"[dim]• {interpretations[cmd]}[/dim]")
    else:
        # 未知命令标记
        lines.append(f"[dim]• Command: [italic]{cmd}[/italic][/dim]")

    # 文件路径提取
    file_paths = [
        p
        for p in parts[1:]
        if (p.startswith(("./", "/", "~")) or "." in p or "\\" in p)
        and not p.startswith("-")
    ]
    if file_paths:
        paths_str = ", ".join(file_paths[:5])
        if len(file_paths) > 5:
            paths_str += f" … and {len(file_paths) - 5} more"
        lines.append(f"[dim]• Paths: {paths_str}[/dim]")


def _preview_write_file(action_input: dict, lines: list[str]) -> None:
    """为 write_file 工具显示路径和内容预览。"""
    path = action_input.get("path", "")
    content = action_input.get("content", "")
    lines.append(f"[bold]File:[/bold] {path}")
    if content:
        # Show first/last lines of content
        content_lines = content.split("\n")
        display_lines: list[str] = []
        for cl in content_lines[:10]:
            display_lines.append(f"  {cl[:200]}")
        if len(content_lines) > 10:
            display_lines.append(f"  … ({len(content_lines) - 10} more lines)")
        lines.append("[bold]Content:[/bold]")
        lines.extend(display_lines)


def _preview_search(tool_name: str, action_input: dict, lines: list[str]) -> None:
    """为搜索类工具显示查询信息和路径。"""
    if tool_name == "grep":
        pattern = (
            action_input.get("pattern")
            or action_input.get("query")
            or action_input.get("input", "")
        )
        path = action_input.get("path") or action_input.get("include", "workspace")
        lines.append(f"[bold]Pattern:[/bold] {pattern[:200]}")
        lines.append(f"[bold]Search in:[/bold] {path}")
    elif tool_name == "glob" or tool_name == "find":
        pattern = action_input.get("pattern") or action_input.get("path", "")
        lines.append(f"[bold]Pattern:[/bold] {pattern[:200]}")
