# PROGRESS

更新时间：2026-10-16（北京时间）
当前阶段：第二阶段补充多股票与深色主题 stage2-multi1 已交付，等待验收。第三阶段未开始。
仓库：https://github.com/kri41106/a-share-ai-butler
续作先读本文件；候选选择与许可证评估见 EVALUATION.md，不必重新全面调研。

## 第二阶段补充：多股票与深色控件（2026-10-16，stage2-multi1）

- 在原持仓模块增量扩展多股票；同代码唯一，逐只修改、锁定、归档及恢复，新增锁定仓固定为0。归档保留历史，不执行交易。
- 同一路径旧库 v1 在升级前用 SQLite backup 自动备份（含 WAL），事务迁移到 v2；保留真实数值、旧表及历史 JSON/ID。重复启动不重复导入，迁移/备份失败回滚并报错，不重置数据。详见 UPGRADE_MULTI.md。
- 每只股票持仓ID与代码绑定卖出数量校验，不能借用其他股票策略仓；锁定仓仍需独立页面和一次性二次确认。
- 首页可滚动多股票列表、总市值/浮盈/数量、行情覆盖和缺失提示。批量复用新浪/东财请求，选中行不重复请求；后台不写持仓。
- 仅对持仓面板及其确认弹窗设置深色 palette/QSS，输入框、选择/焦点、占位/禁用、下拉内容明确配色，不覆盖原浮窗的全局主题。
- 新增19项业务与10项UI测试。持仓相关73项通过；含原有关联模块合计199项通过。覆盖迁移/历史/幂等/WAL备份/故障回滚、两股票隔离、重启、归档恢复、部分行情汇总、深浅系统调色板以及无交易路径检查。
- 源码离屏完整 App 启动、4个 Designer 生成文件一致性检查通过；离屏预览已检查首页及编辑页可读性。
- 本次未更换依赖版本；在新隔离目录复用相同 PySide6/Qt 6.11.1、Shiboken6 6.11.1，保留 winfix1 排除错误 ICU DLL 的规则。
- Windows EXE 已构建成功，当前 Windows 11 沙箱以隔离 PATH/临时 AppData 实际启动成功：frozen=true、qt_platform=windows、ICU来自System32、panel_visible=true、event_loop_exit=0。不是干净虚拟机或用户电脑人工验收；Windows 10 尚未验证。
- 新 ZIP 完整性、QtCore/Qt6Core/Shiboken 文件及错误 ICU 排除检查通过；从 ZIP 全新解压后实际 EXE 再次启动通过。产物 a-share-ai-butler-stage2-multi1-windows.zip。
- 升级必须先从托盘退出旧程序，完整解压新 ZIP 到新目录，双击 StockWidget.exe；不要覆盖 AppData，不要拿旧 EXE 继续写升级后的数据库。
- 待用户实机检查：旧持仓保留、添加第二只及重启、锁定/归档/恢复确认、Windows 11 系统深浅主题与高DPI输入框。未进入第三阶段。

## Windows 启动修复 winfix1（2026-10-16）

- 用户实机验收发现旧包 QtCore ImportError（找不到指定的程序），旧包的构建成功不能代表可启动。
- 已复现：隔离 Python 路径后加载旧包 QtCore.pyd，出现相同 ImportError。
- 证据：旧 Analysis-00.toc 从构建环境 Poppler/Library/bin 收集 icuuc.dll；该 DLL 缺少 Qt6Core 请求的无版本后缀 ICU 函数。Windows System32 的 icuuc.dll 提供所需的全部20项 ICU 导入。
- QtCore.pyd、Qt6Core.dll、Shiboken6 均存在且为 x64；随包 MSVC 文件版本14.44.35211.0。没有依据要求用户先安装 VC++。
- 最小打包修复：StockWidget.spec 排除错误的 icuuc.dll 和其引入的 icudt78.dll，沿用 Windows 系统 ICU。不改 Python/PySide/Qt 版本，不改持仓数据库、盈亏或锁定仓业务。
- 增加 --startup-check 诊断入口，使用临时 AppData，不读取个人持仓；只隔离自启注册表操作，Qt 和实际应用窗口照常初始化。检查结束用 exit(0)，避免托盘窗口拒绝关闭影响自动检查。
- 新增实际冻结 EXE 验证脚本及 Windows workflow 构建后启动门禁；清理 PATH/PYTHONPATH/Qt 插件环境，使用 Windows Qt 平台、临时工作目录并检查窗口与事件循环。
- 已验证构建后的真实 EXE：frozen=true、Qt=6.11.1、qt_platform=windows、icu_from_system32=true、panel_visible=true、event_loop_exit=0。
- 已从新 ZIP 完整解压到独立目录再次启动实际 EXE，检查同样通过；压缩包完整性与无个人数据库检查通过。新包下载文件名：a-share-ai-butler-stage2-winfix1-windows.zip。
- 测试环境为当前 Windows 11 build 26200 x64 沙箱，Python 3.12.14；不是新建的干净虚拟机，未单独验证 Windows 10 或所有旧版系统。
- 原有44项持仓及面板回归测试通过；修复不涉及上述业务文件。
- 新包名含 stage2-winfix1，包内含 STARTUP_FIX.txt 和 WINDOWS_STARTUP.md。应解压到全新文件夹，不能与桌面的旧包混用；不覆盖个人 AppData 数据。
- 尚需用户重新进行自己的 Windows 实机人工验收；不进入第三阶段。

## 已完成

- 在 StockWidget 上增加“我的股票管家”中文持仓面板，使用现有 PySide6 与深色调色板。保留原浮窗、系统托盘和打包 spec。
- 首次初始化单股票示例：博纳影业 001330.SZ，1200股、每股成本9.429元、锁定200股、策略1000股。只在首次创建持仓库时初始化。
- 独立数据库：Windows 的 %APPDATA%/StockWidget/portfolio/positions.sqlite3；旧版表 position、position_history；多股票版升级为 positions 和带 position_id 的历史表，保留旧表。与 cache/history.sqlite3 分开，已有数据不被示例覆盖。
- Decimal 计算市值、浮盈和比例；零成本不显示比例，无有效价格不计算。成本口径不含手续费、分红与已实现盈亏。
- 普通编辑允许名称、A股代码、总股数、成本；保留锁定仓。低于锁定仓时阻止保存。
- 锁定仓只能走专门设置页与默认“否”的二次确认；业务模块使用一次性确认请求、版本校验和事务。
- 统一 validate_sell_quantity 检查建议数量不超过策略仓。本阶段没有实际卖出建议、AI、交易执行或券商登录接口。
- 持仓修改及历史记录在同一事务内保存；错误不重置数据库；过期编辑/确认被拦截。
- 复用原新浪/东财请求与解析，在后台线程获取行情。默认60秒，可设置30～3600秒，显示来源、北京时间和过期/失败状态；超过120秒标为过期，不把收盘旧数据当实时。
- 股票/数据源切换后拒收旧请求。行情请求不接触持仓库，只使用证券元数据，无成本/股数上传。
- 现有 Windows workflow 增加本阶段测试、离屏启动、分支推送触发与附许可的打包脚本。没有创建新项目或引入业务框架。
- LICENSE、NOTICE、原作者版权保留；README 和本阶段修改文件标记修改日期；个人数据库/CSV加入 Git 忽略。

## 验证结果

环境：Windows 沙箱，Python 3.12.14。
- 新增31项业务测试 + 13项面板测试：44项通过。
- 新增与相关原有测试合计187项通过。
- scripts/smoke_portfolio.py 离屏装配完整 App 通过：持仓面板、浮窗、设置、图标和事件循环；网络、自启及原生任务栏隔离。
- 原有4个 Designer 生成文件一致性检查、compileall、git diff --check 通过；新面板 .ui 嵌入 qrc，通过 QUiLoader 加载。
- Windows PyInstaller one-dir EXE 已在当前沙箱构建成功，保留 StockWidget.exe 文件名。
- 完整套件496项：10项错误、17项跳过。9项错误来自此前已知的 macOS 迁移用例在 Windows 的符号链接权限/目录链接及文件占用；另1项为代码表测试缺少 pypinyin。未删除测试或改写无关逻辑来掩盖失败。
- 本地运行所需 Qt Essentials 6.11.1、Shiboken6 6.11.1、Requests 2.34.2 已安装；复用完整下载的 Qt wheel，未升级项目依赖。本地未安装无关 Qt Addons；Actions仍使用原 requirements.txt。
- GitHub Actions：推送后查询运行列表仍为空，云端未有本阶段运行结果。连接器没有 workflow_dispatch 接口；已提交推送/手动触发配置，可在仓库 Actions 页面启用并手动运行 Build Windows。本地构建成功不能表述为云端构建通过。
- winfix1 已通过当前 Windows 环境的冻结 EXE 自动启动检查；用户桌面交互、托盘恢复、高 DPI、多显示器与 Windows 10 人工验收仍未完成，不能声称全部可用。

## 使用与个人验收

- 完整解压验收 ZIP，双击 StockWidget.exe。首页“修改持仓”仅修改本地记录。
- “设置”中的锁定仓修改需再次确认；总股数不能低于锁定仓。可测试取消和确认两种情况。
- 关闭面板后通过托盘“我的持仓”恢复；托盘“退出”结束。退出重启确认最新持仓仍在。
- 断网测试“暂无行情/显示上次行情”、行情时间及过期标记；刷新失败应不影响持仓。
- 截图/日志或反馈前遮盖个人信息，不提交 positions.sqlite3 或其 WAL/SHM 文件。
- 本地示例数据不是东方财富账户持仓，软件不会执行交易。

## 已知边界与后续

- 现已支持多股票；无交易登记、CSV导出、变更历史查询界面、新闻、提醒或模型调用；历史保存在本地表。
- A股输入校验使用沪深北代码段，不代表已实时核验上市状态；用户应核对名称/代码。
- 原高级浮窗设置仍保留上游市场选择入口；新持仓面板拒绝港美股、基金等代码。
- 原浮窗全部隐藏时其刷新仍会暂停，但持仓面板自己的定时读取继续；未开展第三阶段的提醒/通知功能。
- 目录名和关于/更新入口仍沿用 StockWidget，上游代码列表也从原来源获取。后续正式发布前应隔离品牌/数据路径并调整更新入口。
- Qt 共享库、LGPL/GPL文本、第三方依赖许可及对应上游源代码信息随个人验收包附带；再次公开分发前核对最终每个组件的许可与源码提供方式。
- 下一步只响应用户验收意见。第三阶段必须由用户明确授权。

