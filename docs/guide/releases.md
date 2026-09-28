# 版本与发布

Cade 目前通过 Git 安装；`pyproject.toml` 中的 `cade-agent` 版本是包版本的唯一来源。`0.2.0` 正在准备发布，仓库还没有 Cade 的版本标签，因此版本号暂时不能用来定位某次发布。

## 版本规则

- 开发期间不按提交次数递增版本；准备发布时才修改版本号。
- 在 `0.x` 阶段，修复且不改变现有用法时增加修订号（例如 `0.2.0` → `0.2.1`）。新增功能或改变现有用法时增加次版本号（例如 `0.1.2` → `0.2.0`）。
- 不兼容的 CLI、配置、会话格式或 Python 接口变更应在发布说明中明确列出。只有准备承诺稳定的外部使用方式时才发布 `1.0.0`。
- 每个发布版本只对应一个提交和一个同号 Git 标签：包版本 `0.2.0` 对应标签 `v0.2.0`。不要移动或复用已发布的标签。

`uv.lock` 纳入版本控制，供开发和 CI 使用。它锁定仓库环境，但从 Git 安装 Cade 的用户仍按包元数据解析依赖；固定 Git 标签首先固定的是 Cade 源代码。

## 日常检查

CI 在 pull request、`main` 更新和 `v*` 标签推送时运行。`checks` 作业使用 Python 3.12，安装终端与 Linux 沙箱依赖，按锁文件安装 Python 依赖，并执行 Ruff、Pyright、默认 pytest 和分发包构建。测试步骤使用无效的 API key 占位值，不连接真实模型。标签构建还检查标签名与 `pyproject.toml` 的版本一致。推送工作流后，可在 GitHub 仓库设置中将 `checks` 设为 `main` 的必需状态检查。

本地可用相同的只读检查确认提交状态：

```sh
uv sync --locked --extra dev
uv run --locked ruff check src/
uv run --locked ruff format --check src/
uv run --locked pyright src/
uv run --locked pytest src/cade/tests -q --tb=short
uv build --no-sources
```

## 发布步骤

1. 确认 `pyproject.toml` 中待发布的版本；当前准备的是 `0.2.0`。后续发布如需升级，运行 `uv version <新版本> --no-sync`，并检查 `pyproject.toml` 和 `uv.lock` 的改动。
2. 更新 `CHANGELOG.md` 中的待发布条目，写明日期、主要变化及不兼容变化。
3. 运行上述检查，将版本、锁文件和更新日志一起提交；等待 `main` 的 CI 通过。
4. 在该提交创建 `v<新版本>` 标签并推送。确认标签 CI 通过后，在 GitHub 创建对应的 Release，并把 README 的安装命令固定到该标签。

仓库暂不自动向 PyPI 发布。决定通过 PyPI 分发时，再增加独立的发布流程和相应的安装说明。
