# Sessions, file undo, and context rollover

Sessions save input, model responses, tool interactions, and run results. The default location is the project's `.cade/sessions/` directory. Recovery rebuilds the active branch's messages, goal, and pending input, then continues with the current runtime. See [State ownership and recovery](../architecture.md#state-ownership-and-recovery) for the record protocol.

## Recovery and branches

```bash
cade -c                 # 恢复当前项目最近会话
cade --resume           # 从会话列表中选择
cade --session ID       # 恢复指定会话
```

In the interface, use `/resume` to select a session, `/resume ID` to recover a specific session, or `/new` to start a session. Recovery uses persisted facts. Tools interrupted before completion might already have changed workspace files or external services. Check their current state before continuing.

`/fork` opens a menu of user inputs on the current branch. `/fork N` selects the Nth input, counting from one; `/fork ENTRY_ID` selects a specific user input record. The new session retains the history before that input. The selected input returns to the TUI editor for revision and submission. Later input and results remain in the parent session.

Rollover records retain their source-entry IDs and a versioned retention-policy recipe. Replay reconstructs the active window from those facts; the generated message projection is not written to new built-in rollover records. Older sessions with serialized `replacement` windows remain readable. The new branch inherits undo snapshots according to the number of completed tasks retained. Fork creation records the disposition of pending input in the prefix; the editor submits the selected input.

`/clone` copies the complete session log and snapshot repository, then switches to the copy. The success message shows its title and ID. The copy retains the parent session ID. Later input, renaming, and undo affect the copy. Both fork and clone share the current workspace files: branch history comes from the selected prefix, while workspace content comes from the current disk state.

`/tree` shows the current session's entry tree, including active and side branches. Selecting a node moves the current head to that entry and rebuilds context and pending input.

`/rewind N` removes input and results from the most recent N user task turns, defaulting to one. Steering belongs to its task turn. Rewind moves the branch head, records pending-input disposition, rebuilds active context, and trims the snapshot index to the retained completed-task count. Workspace files remain unchanged. Original transcript entries remain in storage. The result reports the actual number of task turns rewound.

## File undo

The TUI captures snapshots before and after each user task turn and records added, changed, deleted, and skipped files in an undo index. Snapshots use an independent Git object store under `.cade/snapshots/<session_id>/`. The current implementation requires a Git project and leaves the project's Git index and HEAD unchanged. Session recording through Python `ask()` and host snapshots are separate capabilities; the calling host determines snapshot coverage.

```text
/undo --list            # 查看快照轮次、文件数和跳过数
/undo                   # 撤销最近一个有快照的用户轮次
/undo 3                 # 按逆序撤销最近三个可撤销轮次
```

Undo checks each file against the turn's ending snapshot, then checks paths and write permissions. Files changed after that turn are reported as conflicts and skipped. Their records remain available for another attempt; files that pass the checks can be restored first. Undo restores modified and deleted files and removes files added during the turn.

Restoration uses the current runtime's mode, rules, grants, approval, and hooks. File conflicts are checked again after approval. When retrying a partially completed turn, files already restored count as complete. Once all files are restored, the turn is marked undone. Results list restored files, conflicts, and failures. N must be a positive integer. Invalid arguments display usage without changing files or snapshot records.

Exclusion rules and a per-file size limit constrain snapshot coverage. By default, snapshots skip `.git/`, `.cade/`, dependency and build directories, several binary formats, and files larger than 10 MiB. Snapshots capture the workspace difference across a turn, so concurrent external edits can also appear. See [SnapshotStore](../../src/cade/harness/snapshot.py) for the scope. Use `/rewind` for conversation history and `/undo` for files.

## Context rollover

Automatic rollover uses the effective input budget and predicted usage. It retains the current request, persistent tool state, and bounded recent complete interactions. It recollects project instructions, NOTE content, verification facts, and runtime state. Original content remains in session records and artifacts for retrieval through `history`. See [Runtime and budget fields](configuration.md#runtime-and-budget-fields) and the [context policy](../context-policy.md).

`NOTE.md` records the current goal, confirmed decisions, validation results, unresolved issues, and next actions. The user or model maintains it. The collector reads the current file. Rollover can record a note snapshot; if the file is missing or changed on recovery, the old snapshot and a verification notice are supplied to the model.

```text
/context                # 查看当前预算、占用和换窗状态
/rollover               # 检查非空 NOTE.md 后开启新窗口
/rollover --force       # 跳过 NOTE.md 检查
/compact                # 开启新窗口并保留最近用户请求
```

Manual `/rollover` clears the ordinary conversation projection and reloads context using the existing NOTE. Use `/compact` to retain the most recent request. Subsequent history retention depends on the rollover path. Original session content remains queryable.

## Read original history

The model can use `history` to browse old-window indexes, search facts, and page through full content by entry ID. Search includes user intent, assistant messages, and tool facts by default. Set `include_derived=true` to include derived reminders, audit copies, and history echoes. Search returns short excerpts and character offsets. Read by ID or browse adjacent records for full content.

History output also uses the tool evidence quota. If a page is trimmed, request a smaller page at the same offset. Continue only after reading that page completely. Full artifact results support source recovery; current-request previews control the budget.

## Side questions

`/btw <question>` answers from a copy of the current model context through an independent provider using the current main-model configuration and no tools. You can ask while the main task continues. The question and answer appear in the TUI. A command event records the invocation; agent conversation history and task snapshots remain tied to main-task turns.
