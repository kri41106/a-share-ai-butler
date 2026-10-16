"""离屏装配完整 App（网络、自启和原生任务栏隔离），不等于 Windows 实机验证。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QTimer
from stockwidget.app import App
from stockwidget.ui.portfolio import QuotePoller


def main():
    with TemporaryDirectory() as directory, \
         patch("stockwidget.core.config_store.config_paths", return_value=directory), \
         patch.object(App, "_start_network_tasks"), \
         patch.object(App, "_start_codes_initialization"), \
         patch.object(App, "set_start_on_boot"), \
         patch("stockwidget.app.TaskbarController"), \
         patch("stockwidget.app.QMessageBox.critical", side_effect=AssertionError("启动出现错误对话框")), \
         patch("stockwidget.ui.floating.presenter.QuotePresenter.refresh"), \
         patch.object(QuotePoller, "fetch"):
        app = App([])
        assert app.portfolio is not None and app.portfolio.isVisible()
        assert app.portfolio.position.strategy == 1000
        assert not app.windowIcon().isNull()
        app.open_settings()
        assert app.settings_dlg.isVisible()
        QTimer.singleShot(100, app.quit)
        assert app.exec() == 0
        app.portfolio.timer.stop()
        app.portfolio.clock.stop()
        app.portfolio.hide()
        app.settings_dlg.close()
        app.win.close()
        app.tray.hide()
        print("PASS: offscreen App, portfolio, floating window, settings, icons and event loop")


if __name__ == "__main__":
    main()
