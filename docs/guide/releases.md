# 版本与发布规范

Cade 遵循严格的语义化版本规范（[Semantic Versioning 2.0.0](https://semver.org/)）与敏捷演进机制。

---

## 1. 版本号命名格式

版本号格式为 `MAJOR.MINOR.PATCH`（例如 `0.2.0`）：
- **MAJOR（主版本号）**：当架构发生重大不兼容断代调整时递增；
- **MINOR（次版本号）**：新增向后兼容的特性、新增工具或重构模块时递增；
- **PATCH（补丁版本号）**：向后兼容的 Bug 修复、安全加固或文档修正时递增。

---

## 2. 检查当前版本

在终端中运行：

```bash
cade --version
```

或使用 Python 包管理器查看：

```bash
uv pip list | grep cade-agent
```

---

## 3. 发布与打包流程（维护者指南）

Cade 采用 `uv` 与 GitHub Actions 进行全自动化 CI/CD 构建与验证：

1. **更新包版本**：在 `pyproject.toml` 中更新 `version` 字段；
2. **提交并打标签**：
   ```bash
   git commit -am "chore: release v0.2.0"
   git tag v0.2.0
   git push origin main --tags
   ```
3. **CI 自动化验证**：GitHub Actions 触发 `ci.yml`，执行：
   - 校验 Git 标签与 `pyproject.toml` 版本一致性；
   - 运行 Ruff Lint 与格式化检查；
   - 运行 Pyright 严格类型检查；
   - 运行命令行入口启动检查；
   - 执行 `uv build --no-sources` 构建分发包。
