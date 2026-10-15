# Changelog

Major features and compatibility changes by version.

## 0.3.0

### Added and improved

- Switch execution modes with Shift+Tab or `/mode`, with consistent tool and operation permission rules.
- Improved account login, default provider configuration, and model and reasoning effort selection.
- Added a project memory directory and runtime configuration through `/context-window`.
- Show time to first token (TTFT) and generation speed in tokens per second (TPS) for each response.
- Simplified agent result streaming, context assembly, and session interfaces.

## 0.2.0

### Added and improved

- Renamed the project to Cade, with the `cade-agent` package and `cade` command.
- Added a browser workspace, session recovery, and an `exec` command for automation.
- Improved the terminal interface, context window management, and session persistence.
- Added a Linux bubblewrap sandbox and operation approval.
- Established a release workflow, dependency locking, and continuous integration checks.

### Compatibility

- The `xcode-agent` package and `xcode` command have been renamed. Install Cade before using the new command.
