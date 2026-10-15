/permission

/config only configures the provider

Context assembly and compaction

## Current `/clone` behavior

`/clone` creates an independent session, copies the complete session log and snapshot repository, and switches to the copy.
The copy's metadata records the parent session ID. The success message shows the copy's title and ID.
The TUI restores history, mode, and context statistics from the copy. Subsequent input, renaming, and undo operations affect the copy.
The parent retains the history and snapshots from the time of cloning. Both sessions use the same workspace files.

Validate this behavior in an isolated Git project. Make three rounds of real file changes, then clone the session. Check both IDs, parent-child metadata, three snapshots in each session, renaming and subsequent input in the copy, and recovery after exit.
