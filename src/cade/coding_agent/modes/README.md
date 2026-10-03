# Cade 产品运行模式

`cade` 与 `cade tui` 使用 [tui/](tui/README.md) 提供终端交互。
`cade exec` 使用 [exec_mode.py](exec_mode.py) 执行单次任务，通过文本或 NDJSON
输出运行时事件，并提供自动化审批和稳定退出码。

两种模式调用同一个编码产品与 Harness。公共应用服务在
[interaction/](../interaction/README.md)，命令行入口处理在 [cli/](../cli/README.md)。
[server/](../../server/) 为浏览器提供 HTTP、WebSocket 和事件展示。

TUI 适配器拥有 prompt-toolkit、Rich、布局、按键、折叠、审批表单与配置向导。
exec 模式直接消费运行时事件，拥有自动化输入输出协议。
