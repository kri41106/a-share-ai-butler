"""真实冻结 EXE 的启动检查；使用临时数据，不触碰个人持仓。Apache-2.0。"""
from pathlib import Path
import json
import os
import platform
import sys
import tempfile
import traceback


def run(report_file):
    report_path = Path(report_file).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    result = {"build": "stage2-winfix1", "ok": False, "frozen": bool(getattr(sys, "frozen", False)),
              "python": platform.python_version(), "windows": platform.platform()}
    def stage(name):
        result["stage"] = name
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        stage("initializing")
        import ctypes
        with tempfile.TemporaryDirectory(prefix="butler-startup-", dir=report_path.parent,
                                         ignore_cleanup_errors=True) as directory:
            os.environ["APPDATA"] = str(Path(directory) / "AppData")
            os.environ["QT_QPA_PLATFORM"] = os.environ.get("BUTLER_STARTUP_QPA", "windows")
            # Exclude this OS side effect from the check; Qt and all application
            # widgets still start normally, with actual packaged dependencies.
            import stockwidget.platform.autostart as autostart
            autostart.set_start_on_boot = lambda *_args: None
            stage("importing_qt")
            from PySide6.QtCore import QTimer, qVersion
            from PySide6.QtGui import QGuiApplication
            from PySide6.QtWidgets import QMessageBox
            # A genuine startup error must fail the check, not wait forever in
            # a dialog that requires a person to click it.
            def critical(_parent, title, message, *_args, **_kwargs):
                raise RuntimeError(f"{title}: {message}")
            QMessageBox.critical = critical
            stage("importing_application")
            from stockwidget.app import App

            stage("constructing_application")
            app = App([sys.argv[0]])
            result["qt"] = qVersion()
            result["qt_platform"] = QGuiApplication.platformName()
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetModuleHandleW.restype = ctypes.c_void_p
            kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
            buffer = ctypes.create_unicode_buffer(32768)
            kernel.GetModuleFileNameW(kernel.GetModuleHandleW("icuuc.dll"), buffer, len(buffer))
            result["icu_from_system32"] = Path(buffer.value).parent == Path(os.environ["SystemRoot"]) / "System32"

            def finish():
                result["panel_visible"] = app.portfolio is not None and app.portfolio.isVisible()
                result["ok"] = result["panel_visible"] and result["icu_from_system32"]
                stage("closing_after_startup_check")
                # Test-mode exit must not be vetoed by close-to-tray windows.
                app.tray.hide()
                app.exit(0)

            QTimer.singleShot(1200, finish)
            stage("running_event_loop")
            result["event_loop_exit"] = app.exec()
            result["ok"] = result["ok"] and result["event_loop_exit"] == 0
            app.portfolio.timer.stop() if app.portfolio else None
            app.portfolio.hide() if app.portfolio else None
            app.win.hide()
            result["stage"] = "complete"
    except BaseException:
        result["error"] = traceback.format_exc()
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1
