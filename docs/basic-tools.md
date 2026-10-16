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
- **Write:** `write(path, content)` creates parent directories if necessary and writes exactly the supplied UTF-8 text. Overwriting a file does not inherit an existing BOM or require the old contents to be readable. Return the written path.
- **Edit:** `edit(path, edits=[{old_text, new_text}, ...])` requires an existing text file. Every old_text must match a unique region of the original file. The replacements must not overlap, and all checks complete before writing. Legacy single-edit arguments remain accepted by the handler. Missing and ambiguous matches fail before writing. Match line endings independently of LF, CRLF, or CR encoding. Preserve the original line endings of each matched span where possible. For inserted line breaks beyond the matched span, use the nearest applicable line-ending style. Leave text outside each match unchanged, including mixed line endings. Preserve BOM and return a diff that describes the saved file.
- **Bash:** `bash(command, workdir?, timeout_ms?, purpose?)` uses the configured Shell. Preserve timeout, cancellation, and exit status. Stream process output in fixed-size chunks; retain a bounded tail in memory and write larger output to a recoverable log file. `purpose=validation` records workspace fingerprints.

## Invariants

1. Preserve public tool names and parameter names. Project-relative paths remain supported; keep current absolute-path and blocked-path rules.
2. Read is parallel-safe. Write, edit, and bash form sequential execution barriers. Use the existing per-file mutation queue.
3. **No implicit formatting.** Tools must never run Ruff, linters, or other commands after writing. A formatting request must be an explicit Bash call.
4. **No hidden side effects.** Edit changes only the selected text. Never silently correct paths, alter unrelated text, or claim a diff for content other than the saved result.
5. Keep rendering metadata separate from execution. One operation returns one truthful tool result.
6. Leave permission decisions, user approvals, hooks, sandbox selection, and audits in the Harness.

## Regression coverage

Check read pagination, missing paths, binary files, and tool registration; write/create and edit round trips; batch-edit atomicity and overlap validation; rejection of ambiguous edits without filesystem changes; mixed LF/CRLF multiline matching and per-match newline preservation; BOM retention in Edit and exact BOM behavior in Write; large-output streaming and log recovery; cancellation; and exact preservation of unformatted Python files. Compare returned patches against the actual saved content.

Run static checks, regression tests, and startup checks from [Testing](testing.md).