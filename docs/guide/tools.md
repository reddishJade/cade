# Tools and execution scheduling

Cade's coding tools declare execution behavior explicitly. The scheduler batches parallel tools and serializes the remaining calls.

## Built-in tools

| Tool | Scheduling | Key parameters | Purpose |
| --- | --- | --- | --- |
| `read` | Parallel read pool | `path`, `offset`, `limit` | Read file content with line ranges and pagination |
| `edit` | Serial write barrier | `path`, `edits[]` (each `old_text`, `new_text`) | Replace matching text with stale-write checks |
| `write` | Serial write barrier | `path`, `content` | Create a file or replace its complete contents |
| `grep` | Optional parallel read tool | `pattern`, `path`, `glob`, `max_results`, `ignore_case`, `literal`, `context` | Search text or regular expressions, preferring ripgrep |
| `glob` | Optional parallel read tool | `pattern`, `path`, `max_results` | Match paths with patterns such as `src/**/*.py` |
| `find` | Optional parallel read tool | `pattern`, `path`, `max_results` | Find project files by partial filename |
| `ls` | Optional parallel read tool | `path`, `limit` | List files and directories |
| `bash` | Serial | `command`, `timeout_ms`, `workdir`, `purpose` | Run shell commands in the Linux sandbox or local environment |
| `webfetch` | Serial | `url`, `format`, `timeout` | Fetch a web page and extract text or Markdown |
| `websearch` | Serial | `query`, `numResults`, `livecrawl`, `type`, `timeout` | Search external documentation and solutions |
| `question` | Interactive gate | `questions` array of objects with `question` and optional `header`, `multiple`, `options` | Ask the user a structured question |
| `delegate` | Delegation | `description`, `prompt`, `mode` / `session_id` / `tasks` | Create, continue, or batch session-backed child agents |
| `history` | Serial read | `operation`, `entry_id`, `session_id`, `offset`, `max_chars` | Search the current branch; read explicit session/entry references and ancestor context without moving the head |
| `save_memory` | Serial write barrier | `path`, `markdown`, `sources`, `expected_content` | Validate explicit history sources and atomically save Markdown in `.cade/memory/`; valid sources do not prove a conclusion |

## Parallel reads and serial barriers

A model response can contain several tool calls, such as reads of four files. Only tools with `ToolSpec.execution_mode="parallel"` enter a parallel batch. Currently, `read` and the optional `grep`, `glob`, `find`, and `ls` tools explicitly declare parallel execution. Other tools default to sequential execution.

The scheduler follows the tool contract rather than inferring whether a tool is read-only. Sequential tools, including `edit`, `write`, and `bash`, wait for earlier parallel work to finish. Writes run in order, preventing concurrent scheduler-managed writes to the same file.

## Read-before-edit checks

The `edit` tool checks that the model is not editing stale content:

1. Every `edits[].old_text` must match uniquely in the original file; overlapping edits are rejected.
2. Cade records the file version when it is read. If the file changes before the edit, the operation is rejected:
   ```text
   File modified since last read. Please read again before editing.
   ```
3. Successful changes can be restored through the snapshot mechanism with `/undo`.

During TUI approval, edit previews label each truncated old/new text with the displayed and original character counts. Use the existing `Ctrl+O` tool-detail toggle to show the complete, JSON-escaped replacement text before approving.
