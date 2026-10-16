
# -*- coding: utf-8 -*-
"""
StockWidget
极简透明盯盘 Widget 浮窗, 按指定股票代码实时显示行情表格

Copyright © 2026 sbr0574

官方仓库地址 Official website:
https://github.com/sbr0574/StockWidget
https://gitee.com/sbr0574/StockWidget

邮箱 Email:
sbr0574@qq.com

本项目基于 Apache License 2.0 开源协议发布, 请遵守相关协议
Licensed under Apache License 2.0
Modified 2026-10-16: optional packaged startup check before Qt imports.
"""

import sys

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--startup-check":
        from stockwidget.platform.startup_check import run
        sys.exit(run(sys.argv[2]))
    else:
        from stockwidget.app import App
        app = App(sys.argv)
        sys.exit(app.exec())
