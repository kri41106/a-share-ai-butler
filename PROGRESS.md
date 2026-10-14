# PROGRESS

更新时间：2026-10-14（北京时间）
当前阶段：第一阶段——开源评估与基础导入。第二、三、四阶段未开始。
仓库：https://github.com/kri41106/a-share-ai-butler
方式：原生 Fork sbr0574/StockWidget，main 基线 337fc32c47fdd9d692c38f63526ab3f54963deb1。
续作先读本文件；不要重新调研全部候选。

## 已完成

- 下载并检查三个候选的源代码、许可证、依赖和关键安全行为；选择 StockWidget，详情 EVALUATION.md。
- 实际创建 Fork，保留代码历史、Apache-2.0 LICENSE、NOTICE 与作者版权。
- 仅新增项目说明/评估/进度文档；未修改业务代码、依赖版本和打包方案。
- 未发现基础项目券商 SDK 或真实交易调用路径；没有调用付费 AI，没有上传个人持仓信息。

## 验证（Windows，Python 3.12.14）

- 原有 tests.core.test_watchlist、test_config、test_window_rules：46 项通过。
- 原有 tests.data.test_quotes、test_bars、tests.core.test_quote_presentation：80 项通过。
- 合计上述 126 项通过；涵盖配置保存/读取、自选规范化、行情过期规则、网络错误分类、解析、历史缓存和指标展示，不等于新增持仓功能测试。
- main.py、stockwidget、scripts 的 compileall 与 git diff --check 通过。
- 新浪原有接口单次读取 sz001330 成功，返回博纳影业，数据时间 2026-10-14 15:35:15（收盘后的最近可获取行情，不代表实时更新）。仅验证该次接口可用，不保证持续可用。
- 额外运行 storage_migration + watchlist 共 34 项，9 项错误：macOS 迁移测试在 Windows 遇到符号链接权限限制、目录链接行为和 SQLite 文件占用等问题。保留测试，未改写无关迁移逻辑。
- Qt/窗口启动与完整套件未完成：PySide6 6.11.1 的大型 wheel 下载停滞，另一镜像未提供该版本。保留依赖版本，停止重复尝试。已准备本地隔离启动脚本（位于工作目录外层 work/smoke_startup.py），尚未执行成功。
- 尚未验证实际 Windows 托盘、任务栏、DPI、交互和 EXE；不能宣称最终桌面软件完全可用。
- 原有 GitHub Actions 测试和 Windows 构建配置已随 Fork 保留，本阶段未运行/验证 Actions 构建。

## 已知问题与边界

- 界面仍为 StockWidget；原项目的港美股、基金、期货入口未隐藏。
- 全部行情组件隐藏后会暂停刷新，需要第三阶段改为后台继续监测。
- 程序配置目录/更新入口仍指向 StockWidget；后续发布前隔离本项目数据路径并调整更新入口。
- 现有 SQLite 仅保存历史行情，未实现用户持仓数据库、交易登记、锁定仓或提醒去重。
- Qt 及其他依赖许可证需要在第三阶段实际 EXE 分发时一并核对/附带。
- 用户的持仓修改/盈亏/策略仓/锁定仓/重启持久化、提醒去重与交易禁用专项测试，按第二/第三阶段实现时补齐。

## 下一步

先补做基础 GUI 启动验证（联网环境安装 requirements.txt，生成 Qt resources），无需重新调研。
等待用户明确确认后才进入第二阶段：中文首页、A 股限定与默认博纳影业、正确盈亏计算、修改持仓、SQLite、锁定仓保护及必要测试。
所有真实交易永远由用户手动执行；AI/任务不能修改锁定仓；付费模型默认关闭。
