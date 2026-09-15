# Cade AI Auth — Provider 认证领域层

本模块定义模型 Provider 的认证契约，并实现 Provider 专有的登录、Token
交换、刷新和请求前凭据解析。

## 边界

- `types.py`：`AuthCredential`、`AuthProvider` 与宿主实现的
  `CredentialStore` 协议。
- `openai_codex.py`：OpenAI Codex OAuth、PKCE、Device Code 和 Token 刷新。
- `registry.py`：Provider ID 到认证实现的注册表。
- `resolver.py`：读取凭据、判断有效期并调用对应 Provider 刷新。

本模块不决定凭据保存路径，不读写 `~/.cade/auth.json`，也不依赖 CLI 或
Harness。文件持久化由 `cade.harness.auth.store.AuthStore` 实现，登录界面由
CLI 提供，`cade.harness.auth.manager.AuthManager` 只负责组装和编排。

依赖方向：

```text
cli -> harness.auth -> ai.auth
          |               ^
          +-- AuthStore --+  implements CredentialStore
```
