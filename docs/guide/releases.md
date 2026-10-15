# 版本与发布

发布包来自一个固定的 Git 提交。版本标签使用 `vMAJOR.MINOR.PATCH`，例如 `v0.3.0`。
标签必须位于 `main` 历史中，且与 `pyproject.toml` 的版本一致。发布后保留标签和产物，修复通过新版本交付。

## 版本号

版本号参考 [Semantic Versioning 2.0.0](https://semver.org/)。`0.x` 仍在开发，不承诺公共接口稳定；破坏性变更递增次版本号，兼容修复递增补丁版本号。
进入 `1.0` 后，破坏性变更递增主版本号，兼容特性递增次版本号，兼容修复递增补丁版本号。
仅重构内部实现不要求递增次版本号。当前发布流程只接受正式版本，不接受预发布标签。

查看源码版本：

```sh
uv version --short
```

查看当前环境安装的版本：

```sh
uv pip show cade-agent
```

## 仓库设置

首次发布前，在 GitHub 配置以下规则：

- 为 `main` 和 `dev` 设置必需的 CI 检查，并限制强制推送和删除。
- 为 `v*` 标签限制创建者，禁止更新和删除已有标签。
- 创建 `release` environment，只允许 `v*` 标签部署，并配置发布审批。单人维护时可允许自审。

规则集可使用的检查名称以实际 CI 运行结果为准。发布工作流声明了 `release` environment，但审批规则需要在 GitHub 设置。
首次运行前先完成设置；仅声明 environment 不会自动启用审批。

## 准备版本

按[开发指南](../development.md#日常产品开发)完成集成。需要递增版本时，在 `dev` 上运行：

```sh
uv version --bump patch
```

按变更选择 `patch`、`minor` 或 `major`。该命令同步更新版本和锁文件。
审查 `pyproject.toml` 与 `uv.lock`，提交版本变更，再将经过验证的版本合入 `main`。
需要真实 Provider 检查时，按[验证准则](../testing.md)执行，并在发布前审查结果和费用。

## 标记版本

确认本地工作区干净，再同步 `main`：

```sh
git switch main
git pull --ff-only origin main
```

读取版本：

```sh
VERSION="$(uv version --short)"
```

创建标签：

```sh
git tag -a "v$VERSION" -m "Release v$VERSION"
```

只推送本次标签：

```sh
git push origin "v$VERSION"
```

标签触发 `Release` 工作流。工作流通过检查和审批后，创建 GitHub Release。
如果检查失败，先修复原因。需要更改源码时使用新版本，不移动已有标签。

## 发布产物

在 GitHub Actions 查看标签触发的 `Release` 运行。
工作流检查标签格式、包版本和 `main` 归属，再对标签对应的固定提交运行产品 CI。
CI 生成 wheel、源码包和 `SHA256SUMS`，临时产物保留 7 天。CI 不会调用模型 API。

检查通过后，按 `release` environment 的规则审批发布。
发布步骤检查标签仍指向同一提交，并核对产物哈希，然后创建 GitHub Release。
发布步骤直接使用本次 CI 构建的产物，不重新构建。GitHub 自动生成发布说明，维护者需要检查内容。

当前流程发布到 GitHub Releases，不发布到 PyPI。
如果发布步骤失败，检查 GitHub 上是否已存在 Release；修正原因后再处理，保留已发布的标签和产物。
