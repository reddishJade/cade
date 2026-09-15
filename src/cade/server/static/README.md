# Cade Web Static — 零构建原生前端

本目录包含 `cade web` 浏览器工作台的完整前端资产，专注于解决：**如何不依赖 Node.js、npm 或繁琐的编译打包工具链，直接以纯原生 Web 技术提供轻量、低延迟、开箱即用的实时交互界面。**

---

## 1. 核心设计与资产分布

```
                  FastAPI Server 静态托管
                             │
            ┌────────────────┼────────────────┐
            ▼                ▼                ▼
        index.html       styles.css         app.js
     (单页骨架与模板)   (现代深浅配色)    (WebSocket/状态机/DOM)
```

### 资产文件职责
- **HTML 单页结构 ([index.html](file:///C:/Users/dwei/workspace/cade/src/cade/server/static/index.html))**：定义全屏三栏布局（左侧会话树与历史列表、中间主步骤与思考流视口、右侧状态与属性面板）及模态审批弹窗骨架。
- **CSS 视觉样式 ([styles.css](file:///C:/Users/dwei/workspace/cade/src/cade/server/static/styles.css))**：无外部 CSS 框架依赖，纯原生 Flexbox/Grid 排版，提供代码高亮、思考折叠框微动效、状态徽章与自适应暗色主题。
- **JavaScript 客户端应用 ([app.js](file:///C:/Users/dwei/workspace/cade/src/cade/server/static/app.js))**：
  - 管理原生 WebSocket `/ws` 连接与断线重连；
  - 实时增量追加思考流与模型文本，自动折叠已完结的长时间推理过程；
  - 渲染工具调用状态卡片（Running / Completed / Failed）与行内 Diff 补丁视图；
  - 接收后端审批事件并在页面弹出交互确认框，点击后实时将决策回传后端。

---

## 2. 架构不变量与设计禁忌

- **零构建分发（Zero-build）**：严禁在此目录引入需要 npm/webpack/vite 等构建步骤的前端框架源码，必须保证该目录直接随 Python Wheel 包开箱分发即可使用。
- **内存与 DOM 节点保护**：长程任务可能产生海量增量事件，前端必须在长流场景下对废弃 DOM 节点或超长文本做保护，防止浏览器标签页崩溃。
