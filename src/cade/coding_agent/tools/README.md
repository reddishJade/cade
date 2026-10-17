# Coding tools

This directory implements code reads, writes, searches, shell execution, and related tools with explicit concurrency and side-effect contracts.

## Tool groups

The normal model-facing coding primitives are `read`, `write`, `edit`, and `bash`. Structured `grep`, `glob`, `find`, and `ls` remain optional. Normal exploration combines native `rg`, `fd`, and `git` commands through `bash`. Product features independently register interaction, web, and runtime tools such as `question`, `webfetch`, `websearch`, and `delegate`.

### Files

- `read.py`: read schema, registration, and bounded file/directory reads.
- `write.py`: write schema, registration, and complete file replacement.
- `edit.py`: edit schema, registration, and unique-match batch replacements against the original file.
- `utils.py`: shared file I/O, path validation, and diff helpers.
- `text_edit.py`: exact replacement and newline handling.
- `file_image.py`: multimodal image support and Base64 extraction.
- `glob_search.py`: optional `glob`, `find`, and `ls`; prefer `fd`, with `rg` or Python fallback.
- `grep_search.py`: optional ripgrep-based content search.
- `file_index.py` / `_search_utils.py`: directory indexes and filtering.
- `bash.py`: sandbox or native shell execution, timeouts, process-group cleanup, and bounded-memory streaming with full-log recovery paths for truncated output.
- `shell_adapter.py`: cross-platform shell detection, command arguments, and environment setup.
- `subagent.py`: delegation, concurrent batches, and result collection.
- `question.py`: user questions and choices.
- `webfetch.py`: fetch pages and convert content to Markdown.
- `websearch.py`: external documentation and web search.
- `truncate.py`: bounded output previews.
- `file_mutation_queue.py`: mutation queues and file locks with completed-key cleanup.

See [Basic tool contract](../../../../docs/basic-tools.md) for tool behavior and ownership.

## Invariants

Read tools explicitly declare parallel safety. Writes and shell tools remain sequential and form execution barriers. I/O failures, missing paths, and syntax errors must return clear tool errors rather than uncaught exceptions that terminate the loop.
