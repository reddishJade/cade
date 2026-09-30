# 测试策略

## 质量标准

测试对象优先是用户实际运行的 Cade 工作流。默认测试套件只保留不能安全地由
E2E 覆盖的针对性回归；不会为了提高断言数量而测试内部对象形状、简单计算或
实现细节。

## 测试层级

### E2E

`src/cade/tests/e2e/` 中的测试使用真实 `build_app()`、真实 session 存储、真实
工具和真实终端边界；只在 provider 的网络边界使用确定性的协议驱动器。每项
测试都要在 `e2e-results/` 保存 JSON trace、复现命令和关键观察结果。

```sh
uv run pytest src/cade/tests/e2e --override-ini 'addopts=' -m e2e -q --tb=short
```

默认命令排除 E2E，因为终端、沙箱和 provider 环境并非每台开发机都有：

```sh
uv run pytest src/cade/tests -q --tb=short
```

### 必要的针对性测试

只有以下行为允许保留窄范围测试：

- 安全策略的硬拒绝、审批范围和路径越界；
- 进程崩溃/撕裂写入后的 session 恢复；
- 凭据和 session 文件的权限保护。

这类测试必须说明 E2E 无法安全或确定地覆盖的失效方式，并断言用户可观察的
安全结果，而不是私有函数的中间值。

### 外部依赖验证

真实 provider、终端 UI、MCP server 和平台相关 shell 行为按需手工验证。
不能用 HTTP 200、mock loader 或另一个服务实例代替用户实际运行路径。

`src/cade/tests/e2e/` 是本地专用测试源码，已由 `.gitignore` 排除，
不会进入 origin 或分发包。它们使用 pytest 临时目录保存 trace、终端截图和
复现步骤；有相应终端、沙箱或 provider 环境时在开发机运行。新克隆的仓库
不包含这些文件，origin CI 只运行静态检查、命令行启动检查和打包。

## 必跑命令

```sh
uv run ruff check src/
uv run pyright src/
uv run pytest src/cade/tests -q --tb=short
```

修改局部行为时先运行聚焦测试，提交前运行当前工作区可用的完整套件。Pyright
的既有 warning 可以单独治理，但新增代码不得增加 error。

## 变更要求

- 新 session event 必须同时有编码、持久化和回放测试；
- 新模型输入必须出现在 `before_provider_request` hook envelope，并改变持久化请求指纹；
- 新工具呈现必须使用类型化 intent，并覆盖两个宿主的共享投影；
- 新组合参数必须由 `build_app()` 的真实测试覆盖；
- composition 输入必须测试发布后隔离，运行时替换必须产生新 generation；
- child 冷恢复必须测试工具能力不扩张，控制必须验证 direct-parent ownership；
- parent 关闭必须测试 child 先取消和释放，且 durable session 不被删除；
- 删除或更改接口时，同一提交直接更新所有调用方与测试，不添加兼容分支；
- 文件和终端依赖使用窄本地协议注入，不能引入远程执行假设。

## 事故回归

每份正式复盘至少产生一个能防止复发的自动化测试，或明确记录为何只能手工
验证。测试名称应描述被保护的不变量，而不只复述具体 bug。
