# 开发指南

产品仓库是 `reddishJade/cade`，内部工具仓库是私有的 `reddishJade/cade-internal`。
产品源码、回归测试和用户文档放在产品仓库。能力评估、A/B 任务、数据集和账户驱动的验收工具放在内部仓库。
产品 CI 和安装包不依赖内部仓库。

## 分支约定

| 仓库与分支 | 用途 |
|---|---|
| 产品 `main` | 已验证、可交付的代码，可以领先于最近的版本标签 |
| 产品 `dev` | 日常集成，通过 CI 后才接收变更 |
| 产品短期分支 | 一项功能、修复或文档变更，通过 PR 合入 `dev` 后删除 |
| 内部 `main` | 当前可用的评估工具 |
| 内部短期分支 | 评估方法、任务或实验变更，完成后合并或删除 |

产品短期分支使用 `feat/主题`、`fix/主题`、`test/主题`、`docs/主题` 或 `chore/主题`。
`main` 和 `dev` 长期保留。版本使用 `vX.Y.Z` 标签，不为每个版本创建长期分支。
产品研究原型放在内部仓库，不再向产品远端推送 `exp/*`、`experiment/*`、`eval` 或备份分支。

## 环境准备

命令使用 Bash。需要 Git、Python 3.12+、[uv](https://docs.astral.sh/uv/) 和 [GitHub CLI](https://cli.github.com/)。
Windows 可使用 Git Bash。Linux Shell 沙箱的依赖见[安装说明](guide/install.md)。

首次配置 GitHub CLI：

```sh
gh auth login
```

克隆产品仓库：

```sh
git clone https://github.com/reddishJade/cade.git
cd cade
```

安装开发依赖：

```sh
uv sync --locked --extra dev
```

安装本地推送检查：

```sh
install -m 755 .githooks/pre-push "$(git rev-parse --git-path hooks/pre-push)"
```

更新 `.githooks/pre-push` 后重新执行安装命令。已有自定义钩子时先合并规则，不直接覆盖。
钩子检查本次推送的新提交历史，阻止内部目录和内部分支误入产品远端。不要用 `--no-verify` 绕过检查。
钩子不替代仓库权限和内容审查；CI 还会检查产品树是否包含内部目录。

## 日常产品开发

先确认工作区干净，再同步集成分支：

```sh
git switch dev
git pull --ff-only origin dev
```

创建本次工作分支：

```sh
git switch -c fix/short-description
```

修改产品代码和对应测试。按[验证准则](testing.md)选择额外检查。
普通 CI 执行以下命令，不调用模型 API：

```sh
uv run --locked ruff check src/cade/
uv run --locked ruff format --check src/cade/
uv run --locked pyright src/cade/
uv run --locked pytest -q src/cade/tests
uv run --locked cade --help
```

格式不符合要求时，先运行 `uv run --locked ruff format src/cade/`，再重跑检查。
只修改文档时，检查内容、链接、命令和差异。

选择本次提交的变更：

```sh
git add -p
```

新增文件使用 `git add -- 文件路径` 暂存。
审查暂存内容：

```sh
git diff --cached
```

提交并推送当前分支：

```sh
git commit -m "fix: describe the corrected behavior"
git push -u origin HEAD
```

创建目标为 `dev` 的 PR：

```sh
gh pr create --base dev --fill
```

等待 CI：

```sh
gh pr checks --watch
```

检查通过后，审查差异、测试证据和 PR 说明。确认可以合并时执行：

```sh
gh pr merge --squash --delete-branch
```

产品 PR 统一使用 Squash，避免把工作分支的同步提交带入集成历史。
不要直接在 `main` 开发，也不要把未验证的代码快进到 `main`。

工作期间 `dev` 有更新时，在工作分支运行 `git fetch origin`，再运行 `git merge origin/dev`。
解决冲突后重跑检查并正常推送。不要强推共享分支。尚未推送的本地提交可以 rebase。

## 内部评估与真实验收

普通贡献者不需要内部仓库。获授权的维护者可在产品仓库的同级目录克隆它：

```sh
cd ..
git clone https://github.com/reddishJade/cade-internal.git
cd cade-internal
```

内部仓库单独提交和推送，不作为产品仓库的子模块。
运行前固定被测 Cade 的提交、模型配置、任务和预算，并确认产品工作区干净。
评估命令、数据约定和真实验收入口见内部仓库的 `README.md`。

可公开的产品回归测试和小型脱敏夹具继续放在 `src/cade/tests/`。
原始会话、凭据、日志、截图、临时工作区和评估结果不进入任何源码 Git 仓库。
公开 CI 的产物也不是私有存储。删除文件或分支不会撤回已公开历史。

## 提升到 main 与发版

先按日常流程把版本变更合入 `dev`。`uv version --bump patch` 会同步版本和锁文件，按变更选择 `patch` 或 `minor`。
等待 `dev` 的 CI 通过，再确认当前集成内容都适合交付。

同步 `dev`：

```sh
git switch dev
git pull --ff-only origin dev
git fetch origin
```

确认可以快进提升：

```sh
git merge-base --is-ancestor origin/main HEAD
```

命令成功后，把这个已通过 CI 的提交提升到 `main`：

```sh
git push origin HEAD:main
```

若快进或保护规则拒绝推送，先查明分叉原因，不使用 `--force`。
这个操作不创建版本。确认正式发布时，再按[版本与发布](guide/releases.md)创建并推送本次标签。
`vX.Y.Z` 标签触发检查、构建和审批，通过后发布到 GitHub Releases。
已发布的标签和产物保持不变。修复走新的 PR 和补丁版本。

## 分支与仓库维护

合并 PR 后删除对应短期分支。仍有进行中 PR 的分支保留；过期研究分支不留在产品远端。
同步远端状态使用 `git fetch --prune origin`。不要使用 `git push --all`、`--mirror` 或批量推送标签。
当前只维护一个版本线，不增加长期的 release、hotfix 或备份分支。

新增或修改仓库 skill 时，描述适用条件、决策边界和完成标准。
把账户配置和真实验收操作放在内部仓库；公开产品文档描述用户可使用的接口和验证要求。

参考：[Codex 产品 CI](https://github.com/openai/codex/blob/160b2b1ef62114eeb41e41e5e3be0000d944b713/.github/workflows/blocking-ci.yml)、[Codex 标签发布](https://github.com/openai/codex/blob/160b2b1ef62114eeb41e41e5e3be0000d944b713/.github/workflows/rust-release.yml)。
