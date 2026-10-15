# Application interaction services

This layer provides host-independent operations for the TUI, browser, and other hosts. It belongs to the coding product and calls assembly and runtime through application contracts.

| Module | Responsibility |
| --- | --- |
| `app_contract.py` | Host-facing application and agent interfaces |
| `commands.py` / `command_handlers.py` | Command definitions, dispatch, sessions, modes, context, and tool operations |
| `completion.py` | Command, model, skill, and file candidates for host input controls |
| `approval.py` | Convert user choices to HITLResult; Harness owns policy and grants |
| `models.py` / `reasoning_effort.py` | Model discovery and effort options |
| `context_stats.py` | Context and usage snapshots for host status displays |
| `file_refs.py` / `skills.py` / `tools.py` | Input expansion, skill activation, and gated tool commands |
| `sessions.py` / `git.py` / `credentials.py` | Session, branch, and credential availability queries |

Dependencies point from modes and browser hosts to product interaction services, then to assembly and runtime. AI owns model adaptation; Agent owns the loop; Harness owns run control, sessions, and events; the coding product owns assembly. Server uses these services rather than duplicating them.

Commands receive `CommandOutput` and do not initiate interaction. Hosts perform selection and input before dispatch. `CommandContext.host_command` supplies authentication, configuration, and other host actions. `coding_agent/modes/tui/` owns Rich, prompt-toolkit, questionary, terminal output and sizing, styles, previews, timing, folding, and input controls.
