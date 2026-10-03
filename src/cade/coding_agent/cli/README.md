# Cade 命令行入口处理

本目录处理编码产品的配置、认证与会话管理子命令。`src/cade/main.py` 负责解析参数、
选择运行模式和管理入口生命周期。

- [auth_cmd.py](auth_cmd.py)：认证命令，用户选择与 API key 向导通过 TUI 呈现。
- [config_cmd.py](config_cmd.py)：配置命令，调用终端配置浏览器。
- [session_cmd.py](session_cmd.py)：持久会话的查询、导出和控制。
- [session_control.py](session_control.py)：exec 与 session 命令使用的跨进程运行状态和中断请求。

运行模式在 [modes/](../modes/README.md)，公共应用操作在
[interaction/](../interaction/README.md)，浏览器宿主在 [server/](../../server/)。
