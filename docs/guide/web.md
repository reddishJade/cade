# Browser workspace

Cade includes a browser interface in addition to the terminal TUI.

## Start the workspace

From the project root, run:

```bash
cade web
```

Cade starts its HTTP/WebSocket service on `127.0.0.1:8787` by default. Open the private URL printed in the terminal, or run `cade web --open`. The URL contains an access token and changes after each server restart.

### Set the host and port

For remote access, bind to the machine's explicit address. Replace this example address with your machine's address:

```bash
cade web --host 192.0.2.10 --port 9000
```

A wildcard bind such as `0.0.0.0` still accepts only loopback Host and Origin values. It does not enable arbitrary remote addresses.

See the [web host reference](../web.md) for access and operational details before exposing the service.

## Interface features

- Expand or collapse reasoning content and view streamed model output.
- Review file diffs with highlighted additions and deletions.
- Browse session history and manage branches and snapshots.
- Approve or deny operations in approval cards, with optional feedback.
