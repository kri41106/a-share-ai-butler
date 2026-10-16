# Windows 启动修复版 winfix1

新包文件名包含 stage2-winfix1，内含 STARTUP_FIX.txt。旧包名只有 stage2，请勿混合解压或只替换 EXE。

## 已定位的原因

旧包中 PySide6、Qt6Core 和 Shiboken6 都存在，均为64位，随包 MSVC 运行库为14.44.35211.0。问题是构建工具从 PATH 上误收集了 Poppler 的 icuuc.dll；该库导出带版本后缀的 ICU 函数，而 Qt 6.11.1 请求的是 Windows 自带的无后缀 ICU API，例如 ucnv_open。Windows 因找不到入口点而使 QtCore 导入失败。

同一旧产物在隔离 Python 路径后复现了用户的 QtCore ImportError；Windows System32 的 ICU 可提供 Qt6Core 请求的全部20个 ICU 导入函数。修复在 spec 中排除错误的 ICU 库及其数据文件，不改变持仓业务，不要求用户先安装 VC++。

验证分开报告：构建成功、真实冻结 EXE 的启动检查、用户实机交互验收。检查命令在临时持仓目录中启动真正的 EXE，清理开发环境的 PATH/PYTHONPATH，加载 Windows Qt 平台、持仓窗口并运行事件循环，随后退出。此检查隔离自启注册表操作，不等于干净虚拟机、Windows 10 或所有硬件的人工验收。

## 新手使用与诊断

1. 完整解压 winfix1 新包到全新文件夹，保留 _internal，双击其中的 StockWidget（类型为“应用程序”）。程序仍沿用原本的本地持仓目录，不会用新包覆盖已有持仓。
2. 如仍启动失败，在程序所在文件夹的地址栏输入 powershell 后按回车。输入：

   `.\StockWidget.exe --startup-check "$PWD\启动诊断.json"`

   等待约10秒，再用记事本打开“启动诊断.json”。此模式只使用临时示例数据，不读取个人持仓，也不操作真实交易。可发送诊断文件中的错误段；不要发送 positions.sqlite3。
3. 如未生成诊断文件，按 Win+R，输入 eventvwr.msc，查看“Windows 日志 → 应用程序”中对应时间的 Application Error，记录“故障模块名称”和错误代码。也可运行 winver 记录 Windows 版本。无需安装第三方 DLL 修复工具。
4. 如果 _internal 文件被安全软件隔离，先查看“Windows 安全中心 → 病毒和威胁防护 → 保护历史记录”，记录被拦截的具体文件。不要关闭系统安全保护，也不要从陌生 DLL 下载站补文件。
