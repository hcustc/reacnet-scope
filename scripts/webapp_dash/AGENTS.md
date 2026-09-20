# Dash 约定

本文件补充根目录 AGENTS.md，适用于 `scripts/webapp_dash/` 及其 assets。

- `app.py` 定义布局，`navigation.py` 定义导航，`callbacks.py` 注册回调。
  数据操作通过 `reacnet_scope.services` 门面；新增化学分析进入核心包。
- 调整控件 ID 时检查布局、导航、Python 回调、clientside JS 和 smoke tests 的引用。
  新控件沿用现有 Dash / Bootstrap 组件与本地 CSS；本仓库没有 Node 构建步骤。
- Dataset Candidate 的浏览/检查不改变 Current Dataset。只有明确加载成功才提交切换；
  失败保留原上下文，取消或被替代请求的迟到结果不能覆盖新选择。
- Current Dataset、页面与选择属于浏览器会话；索引、数据集设置和 Preparation Task
  属于 Dataset Workspace。切换清空旧结果；任务始终绑定原数据集和源修订。
- 每项 Analysis Capability 独立报告状态和恢复动作；页面可打开、存在文件或某项索引
  已就绪不代表整个数据集可完成全部分析。
- 跨页面传递精确 Species / Reaction Type / Occurrence Identity 和必要来源上下文，
  目标页面调用同一核心服务。显示标签、表格行号不能充当证据身份。
- 回调不扫描大型 RNG 工件或同步构建索引；长任务显示进度、禁用重复提交并提供取消。
  目录浏览使用服务端允许根目录校验，不能把客户端目录选择当作服务端授权。
- 运行时资源保持本地可用；不引入 CDN、遥测或数据上传。`3Dmol-min.js` 和
  `bootstrap-local.css` 是随仓库分发的资源，常规页面改动使用自有 CSS/JS。

数据集/回调交互使用 `reacnet-dash-workflows`；纯配色、间距或文案修正通常不需要完整
工作流。验证入口见 [验证指南](../../docs/agents/testing.md)。
