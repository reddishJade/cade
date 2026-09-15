# Cade CLI TUI — 全屏终端工作台

TUI 模块是基于 `prompt-toolkit` 构建的全屏字符终端交互系统，专注于解决：**如何在纯终端环境中提供多窗口、侧边栏会话树、实时思考展开与低视觉噪声的沉浸式编码工作台体验。**

---

## 1. 核心架构与界面状态机

```
                      ┌──────────────────────┐
                      │    run_tui(app)      │
                      └──────────┬───────────┘
                                 │ 初始化 Application & 布局
                                 ▼
                 ┌────────────────────────────────┐
                 │       TuiState (state.py)      │
                 │ (消息流 / 激活视口 / 模态审批)   │
                 └───────────────┬────────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
    [侧边栏]                 [主对话区]              [状态与底栏]
 (会话列表/历史树)       (流式消息/思考折叠/Diff)     (模式/Token用量/输入框)
```

### 核心文件分工
- **应用生命周期 ([app.py](file:///C:/Users/dwei/workspace/cade/src/cade/cli/tui/app.py))**：TUI 主入口 `run_tui`、多窗格布局定义、键盘事件分发器（KeyBindings）与后台任务协程。
- **响应式状态容器 ([state.py](file:///C:/Users/dwei/workspace/cade/src/cade/cli/tui/state.py))**：`TuiState` 集中管理当前分支消息树、自动滚动策略、模态确认窗口及输入缓冲区。
- **字符排版与渲染 ([rendering.py](file:///C:/Users/dwei/workspace/cade/src/cade/cli/tui/rendering.py))**：Markdown 终端格式化、Token 统计指示条及状态徽章着色。
- **复用小部件 ([widgets.py](file:///C:/Users/dwei/workspace/cade/src/cade/cli/tui/widgets.py))**：状态栏、边界框、滚动容器与侧边栏渲染部件。

---

## 2. 架构不变量与设计禁忌

- **状态单一真实源**：界面的一切视觉变化必须经由 `TuiState` 驱动，小部件不得持有相互矛盾的局部状态。
- **渲染异常隔离**：格式化异常或过宽终端输出必须在渲染层内部被裁剪，严禁抛出异常破坏 `prompt-toolkit` 的全屏模式恢复。
