# 开发指南

仓库由 `reddishJade` 一人维护。PR 由你自己创建和合并；产品 PR 用来触发 CI，不需要等别人审批。

| 仓库 | 内容 | 分支 |
|---|---|---|
| `cade`（公开） | 产品代码、回归测试、用户文档 | `dev` 集成，`main` 交付 |
| `cade-internal`（私有） | 能力评估、A/B、真实验收工具和任务 | `main` 保存可用工具 |

## 环境准备

现有目录是 `~/workspace/cade` 和 `~/workspace/cade-internal`。重建产品环境或更新推送钩子时运行：

```sh
cd ~/workspace/cade
uv sync --locked --extra dev
install -m 755 .githooks/pre-push "$(git rev-parse --git-path hooks/pre-push)"
```

## 日常产品开发

从最新 `dev` 开一个短期分支，完成一件事：

```sh
cd ~/workspace/cade
git switch dev
git pull --ff-only origin dev
git switch -c fix/short-description
```

修改后检查：

```sh
uv run --locked ruff check src/cade/
uv run --locked ruff format --check src/cade/
uv run --locked pyright src/cade/
uv run --locked pytest -q src/cade/tests
uv run --locked cade --help
```

检查通过后提交。`git add -p` 处理已跟踪文件，新文件用 `git add -- 路径`：

```sh
git add -p
git diff --cached
git commit -m "fix: describe the change"
git push -u origin HEAD
gh pr create --base dev --fill
gh pr checks --watch
```

CI 通过后自己合并，并删除短期分支：

```sh
gh pr merge --squash --delete-branch
```

## 内部评估与真实验收

内部工具也从短期分支修改：

```sh
cd ~/workspace/cade-internal
git switch main
git pull --ff-only origin main
git switch -c chore/short-description
```

按内部 `README.md` 检查，再提交、推送。创建 PR 时改用 `gh pr create --base main --fill`，然后自己 Squash 合并。
内部仓库没有产品 CI，不自动跑模型。实验前固定 Cade 提交、任务、模型和预算。

## 提升到 main 与发版

需要递增版本时，先在工作分支运行 `uv version --bump patch`，把版本和锁文件一起通过 PR 合入 `dev`。
确认 `dev` 的 CI 通过后，提升同一提交到 `main`：

```sh
cd ~/workspace/cade
git switch dev
git pull --ff-only origin dev
git fetch origin
git merge-base --is-ancestor origin/main HEAD
git push origin HEAD:main
```

正式发版时才打标签。当前版本尚未发布时可以直接使用，否则先递增版本：

```sh
VERSION="$(uv version --short)"
git tag -a "v$VERSION" -m "Release v$VERSION"
git push origin "v$VERSION"
```

标签触发检查和构建。通过后，你在 GitHub 的 `release` environment 批准发布。
已发布的标签不移动，修复发新版本。完整发布规则见[版本与发布](guide/releases.md)。

不要强推 `main` 或 `dev`，不要使用 `git push --all`。内部工具不推到公开库，凭据和原始运行记录不进 Git。
