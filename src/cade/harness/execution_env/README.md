# Execution environments and sandboxing

`execution_env` provides local shell and filesystem access and Linux namespace isolation through bubblewrap.

## Execution model

```text
Command / file action
  → CommandSandbox
    → LinuxBubblewrapSandbox on supported Linux configurations
      - Writable project root and temporary paths
      - Read-only host paths
      - Masked credential paths
      - NetworkAccess-controlled networking
    → SubprocessShell for local process execution
```

### Files

- [filesystem.py](filesystem.py): `FileSystem`, `LocalFileSystem`, path checks, and read/write interfaces.
- [shell.py](shell.py) and [subprocess.py](subprocess.py): `Shell`, timeout handling, environment, and streamed process output.
- [sandbox.py](sandbox.py): `SandboxPolicy`, `SandboxMode`, `NetworkAccess`, and `SandboxedCommand`.
- [linux_sandbox.py](linux_sandbox.py): Linux `bwrap` namespaces for filesystem and network isolation.

## Invariants

Approval and isolation are separate. PermissionEngine authorizes actions; the sandbox restricts their accessible resources. Neither replaces the other.

Linux sandbox configurations mask sensitive private-key and cloud credential paths. Sandbox availability failures must be explicit. The default Linux configuration fails closed if `bwrap` is missing; other platforms use local shells. See the [architecture](../../../../docs/architecture.md#modes-permissions-and-execution-environment) for supported boundaries.
