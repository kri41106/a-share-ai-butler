# 第一阶段评估（2026-10-14）

已下载并检查三个项目的实际源码；只导入 StockWidget，不混合其他项目代码。

| 项目 / 检查版本 | 许可证 | 实际代码与依赖 | 结论 |
| --- | --- | --- | --- |
| [StockWidget](https://github.com/sbr0574/StockWidget) / 337fc32c47fdd9d692c38f63526ab3f54963deb1 | Apache-2.0；包含 NOTICE 与 sbr0574 版权 | PySide6、requests、PyInstaller；中文浮窗、托盘、行情解析、SQLite 历史行情缓存、Windows spec/Actions、现有 unittest | 选作唯一基础；保留代码与历史，后续小范围扩展 |
| [trading_agents_desktop](https://github.com/xiaobei56/trading_agents_desktop) / 4b68782fdc635ccf6ccc53a958a090ba56a93f2e | Apache-2.0 | pyproject.toml 包含 LangChain/LangGraph、多家模型 SDK、AkShare、pandas、Redis、yfinance、backtrader 等；desktop/runtime.py 已有模型配置与中文报告分段；dataflows/akshare_a_stock.py 已有 A 股适配 | 仅参考；首阶段不安装、不移植，不触发付费模型 |
| [AShareTools](https://github.com/yxingg/AshareTools) / 63ea4d4d8a0f620e14c8d271a1801056639bd332 | MIT，Copyright (c) 2025 yxingg | PyQt6、requests、pandas、AkShare、chinesecalendar；已有托盘、多源 K 线、预警引擎与 spec；data_fetcher.py 全局改写 Session 代理行为且 SSL 错误时回退 HTTP；alert_engine.py 动态执行策略模块 | 提醒可作参考；依赖及适配成本较高，PyQt6 通常还涉及 GPL/商业许可，优先避免混用 |

## 来源与许可证

采用 GitHub 原生 Fork，仓库 https://github.com/kri41106/a-share-ai-butler ，保留上游历史、LICENSE、NOTICE、源码版权及 README 原始说明。新增项目说明置于 README 前部并明确修改日期。没有复制 B/C 代码。
StockWidget 的 Apache-2.0 允许修改再分发，但必须保留许可/署名并标记修改文件；本项目继续使用该许可证。Qt/PySide6 有独立 LGPLv3/GPLv3/商业许可条款，打包发布前需核对实际使用组件并随包提供相应许可与声明；主项目许可证不覆盖所有依赖。现有 PyInstaller 构建方案保留，EXE 验证在第三阶段。

## 安全与适配边界

检查 main.py、app.py、core/config_store.py、data/quotes.py、data/update_check.py、platform/autostart.py、Windows 构建配置及运行目录危险调用/交易 SDK 搜索。未发现券商 SDK、下单/撤单、账户密码采集、网络写入交易路径。原生窗口/快捷键 API 是桌面功能；macOS subprocess 用于自启管理。此结论是限定范围源码检查，不是完整安全审计或依赖漏洞扫描。

StockWidget 行情使用新浪/东财 HTTPS，保留超时、证书校验及网络错误分类；会读取上游市场代码列表、检查上游版本。必须在正式发布前调整本项目更新入口，防止用户误下载上游程序。
原项目支持非 A 股市场，隐藏全部行情组件会暂停刷新；这与目标 A 股限定、后台持续监测不同，记录为第二/第三阶段任务。SQLite 已有的是行情缓存，不等于持仓数据库。
原有成本/浮盈显示不能代替用户要求的成本核算、交易登记和锁定仓保护；这些尚未实现。
所有真实交易必须由用户在东方财富手动操作。后续代码不得接入券商交易 API、自动操作券商窗口或存储交易凭据。未公开用户私人持仓数据。

验证结果和下一步见 [PROGRESS.md](PROGRESS.md)。
