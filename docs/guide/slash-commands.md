# Slash commands

In the terminal interface, input starting with `/` is treated as an internal command. Press Tab to complete command names and arguments.

## Session lifecycle

| Command | Syntax | Description | Typical use |
| --- | --- | --- | --- |
| `/new` | `/new` | Create and switch to a new session | Start an independent task |
| `/continue` | `/continue` | Resume the project's most recent valid session | Continue unfinished work |
| `/resume` | `/resume [session_id]` | Resume a session by ID | Return to a specific task |
| `/sessions` | `/sessions` | List project sessions | Inspect task history and update times |
| `/rename` | `/rename <TITLE>` | Set the current session title | Identify tasks in the session list |
| `/exit` or `/quit` | `/exit` | Save state and exit | Finish work |

## Branches and history trees

| Command | Syntax | Description | Typical use |
| --- | --- | --- | --- |
| `/fork` | `/fork [N\|entry_id]` | Retain history before the selected input, switch to a new session, and return that input to the editor | Revise an earlier question and explore another approach |
| `/clone` | `/clone` | Copy the complete log and snapshots, then switch to the copy | Start an independent conversation from current progress |
| `/tree` | `/tree` | Show the session entry tree; selecting a node moves the head and restores state | Browse history and side branches |

## Undo and rewind

| Command | Syntax | Description | Typical use |
| --- | --- | --- | --- |
| `/undo` | `/undo [N]` | Restore file changes from the most recent N undoable user turns in reverse order, reporting each file | Undo unwanted code changes |
| `/rewind` | `/rewind [N]` | Remove input and results for the most recent N user tasks and trim the snapshot index | Remove recent instructions or decisions from active history |
| `/revert` | `/revert [N]` | Restore files, as with `/undo` | Restore files to their pre-task state |

## Execution modes

| Command | Syntax | Description |
| --- | --- | --- |
| `/mode` | `/mode [act\|build\|plan]` | Open the mode list or switch modes; Shift+Tab cycles through act → build → plan |

## Context and rollover

| Command | Syntax | Description | Typical use |
| --- | --- | --- | --- |
| `/context` | `/context` | Show token usage, budget, and rollover status | Check the effective input budget |
| `/rollover` | `/rollover [--force]` | Hand off through `NOTE.md` and start a new window | Reset a long conversation |
| `/new-context` | `/new-context [--force]` | Perform the same NOTE handoff and rollover as `/rollover` | Start a new working window |
| `/compact` | `/compact` | Force window reduction and retain the latest working turn | Clear stale output |

## Models and reasoning

| Command | Syntax | Description |
| --- | --- | --- |
| `/model` | `/model [provider/model_name]` | Open the model and effort picker: select a model with ↑/↓ and effort with ←/→; Enter applies both, Esc cancels. A direct name switches models; a provider prefix selects the provider |
| `/effort` | `/effort [level]` | Select or set a reasoning effort supported by the current model |
| `/context-window` | `/context-window [size\|default]` | Show or limit the current session's window. Accepts `200k`, `200000`, or `1.5m` (k=1,000; m=1,000,000). `default` restores the model window. The limit cannot exceed model support, does not write configuration, and takes effect next turn. If usage exceeds the new rollover threshold, the next turn rolls over |
| `/thinking` | `/thinking [on\|off]` | Toggle Responses/Codex reasoning summaries; use `/effort` for reasoning intensity |

## Authentication and settings

| Command | Syntax | Description |
| --- | --- | --- |
| `/auth` | `/auth [status\|list\|login\|connect\|logout]` | Inspect account status or perform authentication operations |
| `/login` | `/login [provider]` | Start provider login and credential verification |
| `/logout` | `/logout [provider]` | Remove the provider's stored local credentials |
| `/config` | `/config [setting]` | Browse supported settings or open an editor; saved settings apply on later startup |
| `/permissions` | `/permissions [list\|clear]` | Open the permissions menu, inspect effective rules and grants, or clear session grants |
| `/hooks` | `/hooks` | Show external hook configuration sources and recent execution status |

## Tools, extensions, and memory

| Command | Syntax | Description |
| --- | --- | --- |
| `/tool` | `/tool list` or `/tool NAME INPUT` | List current-mode tools or execute one through runtime permission checks; INPUT accepts a JSON object or shorthand for one required argument |
| `/skill` | `/skill NAME [prompt]` | Activate a skill; an optional prompt becomes a follow-up task |
| `/memory` | `/memory` | Show the workspace's `.cade/memory/` location |
| `/mcp` | `/mcp [status]` | Inspect external MCP connectivity; restart Cade after configuration changes |

## Interaction and output

| Command | Syntax | Description |
| --- | --- | --- |
| `/steer` | `/steer <MESSAGE>` | Submit guidance, consumed at the next reasoning boundary during a run; when idle, start a task |
| `/queue` | `/queue [steer\|followup\|interrupt\|message]` | Show or set busy-input policy, or submit a follow-up task; the default policy is followup |
| `/goal` | `/goal [DESCRIPTION\|pause\|resume\|clear]` | Show or set a stop condition; setting one starts a task. Pause, resume, or clear it. An independent acceptance request checks completion |
| `/btw` | `/btw <QUESTION>` | Answer independently from a context copy with no tools while the main task continues |
| `/clear` | `/clear` | Create and switch to a new session, as with `/new` |
| `/help` | `/help` | Show command help |

N is a positive integer and defaults to one. `/undo --list` shows snapshot records. Undo checks per-file conflicts and runtime permissions and reports completed, skipped, and failed items. See [Sessions](sessions.md) for full undo, rewind, branch, and rollover semantics.

```text
/fork 3                 # 保留第三条输入之前的历史，将该输入放回编辑框
/tool read NOTE.md      # 单必填参数的简写
/tool write {"path":"plan.txt","content":"draft"}
/queue followup         # 设置繁忙输入策略
/queue Review the completed changes
```
