# 紧凑导航栏布局

启动脚本默认使用紧凑布局。紧凑布局由同一套页面、
会话状态和回调驱动，将导航与当前 RNG 数据放在顶部，不改变数据切换或分析契约。

## 启动

```bash
./start-reacnet-scope.sh
```

启动脚本在 `REACNET_SCOPE_COMPACT_NAV` 未设置或为空时默认设为 `1`。
如需经典布局（左侧导航 + 顶部数据上下文栏），可显式关闭：

```bash
REACNET_SCOPE_COMPACT_NAV=0 ./start-reacnet-scope.sh
```

直接使用 `reacnet-scope serve` 时，仍需设置 `REACNET_SCOPE_COMPACT_NAV=1`
才能启用紧凑布局。

## 操作与响应式布局

- 顶部提供物种发现、反应路径、证据核查、趋势对比入口，沿用当前导航注册。
- 点击左上角品牌按钮进入 RNG 数据页；“选择数据”打开导入界面；“数据与任务”
  提供数据页与索引状态刷新入口。
- 当前 RNG 数据名称与状态使用两种布局共享的回调组件 ID。导入、切换和准备任务
  沿用现有工作流；单项能力状态不表示全部分析均已就绪。
- 桌面端在同一行显示导航与数据操作，主内容占满可用宽度。
- 900px 及以下将分析导航放在第二行，保留文字标签；长数据名称省略显示。
- 紧凑布局样式限定在 `.rs-compact-shell` 内，不覆盖经典布局的状态徽标。

## 验证

布局与回调 smoke、浏览器 fixture 均覆盖两种布局。浏览器验收命令和依赖安装见
[验证指南](agents/testing.md)。例如只验证导航和窄屏详情：

```bash
REACNET_SCOPE_BROWSER_TESTS=1 uv run --locked --group browser pytest -q \
  tests/browser/test_workbench.py \
  -k 'navigation_preserves_dataset or exact_selection_export'
```

浏览器测试使用临时的小型 RNG 数据，不代表大型真实数据或 Firefox/Safari 验收。
