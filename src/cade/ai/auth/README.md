# Provider authentication

This module defines provider authentication contracts and implements provider-specific login, token exchange, refresh, and pre-request credential resolution.

## Boundaries

- `types.py`: `AuthCredential`, `AuthProvider`, and the host-implemented `CredentialStore` protocol.
- `openai_codex.py`: OpenAI Codex OAuth, PKCE, device authorization, and token refresh.
- `registry.py`: provider ID to authentication implementation.
- `resolver.py`: read credentials, check expiration, and invoke provider refresh.

This module does not choose storage paths, read `~/.cade/auth.json`, or depend on CLI or Harness. `cade.harness.auth.store.AuthStore` owns file persistence. CLI owns login presentation. `cade.harness.auth.manager.AuthManager` assembles and orchestrates authentication.

```text
cli -> harness.auth -> ai.auth
          |               ^
          +-- AuthStore --+  implements CredentialStore
```
