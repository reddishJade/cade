# Core coding tools

This is the stable contract for Cade's `read`, `write`, `edit`, and `bash` tools.

## Ownership

```text
Agent (schema validation and scheduling)
  -> Harness ToolGate (mode, permissions, approval, hooks, audit)
  -> coding_agent/tools (operation and result)
  -> Agent (model-visible result)
```

Each core tool lives in a file named after the tool: `read.py`, `write.py`, `edit.py`, or `bash.py`. The module owns its schema, factory, and operation. `utils.py` contains only helpers shared by file tools. `text_edit.py` owns literal replacement; `path_utils.py` owns path restrictions. `harness/execution_env/` owns filesystem and process adapters. Tool implementations must not duplicate Harness permissions, approval, or audit.

## Contracts

- **Read:** `read(path, offset?, limit?)` reads a file, directory, or supported image. Text offsets start at 1. Preserve line numbers, continuation hints, bounded output, binary detection, and image metadata. No workspace writes.
- **Write:** `write(path, content)` creates parent directories if necessary and creates or replaces one UTF-8 text file. Preserve the existing BOM behavior for overwritten files. Return a diff that describes the resulting file.
- **Edit:** `edit(path, old_text, new_text, replace_all=false)` requires an existing text file. Without `replace_all`, the match must be unique. Missing and ambiguous matches fail before writing. Preserve BOM and LF/CRLF compatibility. Return a diff that describes the resulting file.
- **Bash:** `bash(command, workdir?, timeout_ms?, purpose?)` uses the configured Shell. Preserve timeout, cancellation, exit status, bounded output, and the full-output recovery path. `purpose=validation` records workspace fingerprints.

## Invariants

1. Preserve public tool names and parameter names. Project-relative paths remain supported; keep current absolute-path and blocked-path rules.
2. Read is parallel-safe. Write, edit, and bash form sequential execution barriers. Use the existing per-file mutation queue.
3. **No implicit formatting.** Tools must never run Ruff, linters, or other commands after writing. A formatting request must be an explicit Bash call.
4. **No hidden side effects.** Edit changes only the selected text. Never silently correct paths, alter unrelated text, or claim a diff for content other than the saved result.
5. Keep rendering metadata separate from execution. One operation returns one truthful tool result.
6. Leave permission decisions, user approvals, hooks, sandbox selection, and audits in the Harness.

## Regression coverage

Check read pagination, missing paths, binary files, and tool registration; write/create and edit round trips; rejection of ambiguous edits without filesystem changes; BOM and CRLF retention; cancellation; and exact preservation of unformatted Python files. Compare returned patches against the actual saved content.

Run static checks, regression tests, and startup checks from [Testing](testing.md).