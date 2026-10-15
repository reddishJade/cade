# Browser workspace

Cade includes a browser interface in addition to the terminal TUI.

## Start the workspace

From the project root, run:

```bash
cade web
```

Cade starts its HTTP/WebSocket service and prints the address, which defaults to `http://127.0.0.1:8787`.

### Set the host and port

To bind the service to a remote development machine's or container's network interfaces:

```bash
cade web --host 0.0.0.0 --port 9000
```

See the [web host reference](../web.md) for access and operational details before exposing the service.

## Interface features

- Expand or collapse reasoning content and view streamed model output.
- Review file diffs with highlighted additions and deletions.
- Browse session history and manage branches and snapshots.
- Approve or deny operations in approval cards, with optional feedback.
