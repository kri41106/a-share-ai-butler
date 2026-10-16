# 第三方软件与许可

本项目保留 StockWidget 的 Apache-2.0 LICENSE、NOTICE 与原作者 sbr0574 的版权。

Windows 目录包使用未修改的 PySide6 / Shiboken6 6.11.1 和 Qt 6.11.1 共享库。PySide6 Essentials 的发行元数据声明 LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only；本项目采用 LGPLv3 选项。LGPLv3 与其引用的 GPLv3 正文在 third-party-licenses 中，原文来自 Qt 官方 qtbase v6.11.1 的 LICENSES。Qt 源文件及第三方组件可能具有独立许可，应一并保留发行包中的声明。

库的版权属于 The Qt Company Ltd. 及其贡献者。允许按 LGPL 条款修改、替换这些共享库，并为调试修改后的库进行必要的逆向工程。目录包中的动态库保留为独立文件，不添加阻止替换的限制。

对应上游源代码与构建说明：

- Qt 6.11.1：https://code.qt.io/cgit/qt/qtbase.git/tree/?h=v6.11.1
- Qt tools 6.11.1：https://code.qt.io/cgit/qt/qttools.git/tree/?h=v6.11.1
- PySide/Shiboken 6.11.1：https://code.qt.io/cgit/pyside/pyside-setup.git/tree/?h=v6.11.1
- Qt source archives：https://download.qt.io/official_releases/qt/6.11/6.11.1/single/

打包脚本还随包复制实际环境的 Python、Requests、urllib3、idna、charset-normalizer、certifi 和 PyInstaller 等许可文件与发行元数据。PyInstaller 的启动器有专门例外，详见所附 COPYING.txt。

本次构建只作个人验收；未作为公开 Release 发布。再次公开分发前，需对最终实际打包的每个 Qt/第三方组件核对完整声明与相应源码提供方式，不能只依赖主项目的 Apache 许可证。
