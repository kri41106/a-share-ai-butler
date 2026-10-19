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
    result = {"build": "stage4-action1", "ok": False, "frozen": bool(getattr(sys, "frozen", False)),
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
                center = app.portfolio.risk.open(1) if app.portfolio else None
                result["reminder_settings_visible"] = bool(center and center.isVisible() and center.tabs.currentIndex() == 1)
                result["risk_database_ready"] = bool(app.portfolio and app.portfolio.risk.store)
                from PySide6.QtCore import QEvent,QPointF,Qt
                from PySide6.QtGui import QMouseEvent
                from PySide6.QtWidgets import QApplication,QStyle,QStyleOptionSpinBox
                spin=center.failuresSpin
                before=spin.value();option=QStyleOptionSpinBox();spin.initStyleOption(option)
                rect=spin.style().subControlRect(QStyle.CC_SpinBox,option,QStyle.SC_SpinBoxUp,spin)
                point=QPointF(rect.center());global_point=QPointF(spin.mapToGlobal(rect.center()))
                QApplication.sendEvent(spin,QMouseEvent(QEvent.MouseButtonPress,point,global_point,Qt.LeftButton,Qt.LeftButton,Qt.NoModifier))
                QApplication.sendEvent(spin,QMouseEvent(QEvent.MouseButtonRelease,point,global_point,Qt.LeftButton,Qt.NoButton,Qt.NoModifier))
                result["spin_arrow_increments"] = spin.value()==before+1
                spin.setValue(before)
                briefing=app.portfolio.open_briefing(0,auto_fetch=False)
                result["news_center_visible"] = bool(briefing and briefing.isVisible())
                briefing.tabs.setCurrentIndex(1);app.processEvents()
                result["overview_ready"] = bool(briefing.overviewText.toPlainText())
                briefing.tabs.setCurrentIndex(2);app.processEvents()
                result["chatgpt_prompt_private_default_off"] = not briefing.privateCheck.isChecked() and '9.429' not in briefing.promptText.toPlainText()
                briefing.tabs.setCurrentIndex(3);app.processEvents()
                result["ai_history_visible"] = briefing.tabs.currentIndex()==3
                # Isolated synthetic scenario only; never enters the user's database or clipboard.
                import time
                now=time.time()
                app.portfolio.quotes['001330.SZ']={'current_price':10,'prev_close':9,'timestamp':now,
                    '_code':'001330.SZ','_source':'sina'}
                app.portfolio.render();briefing.privateCheck.setChecked(True);briefing.rebuild()
                result['synthetic_snapshot_export_matches_home'] = ('10.00' in app.portfolio.positionsTable.item(0,1).text()
                    and '685.20' in briefing.promptText.toPlainText() and '不超过200字' in briefing.promptText.toPlainText())
                packet=briefing.store.save_packet(briefing.draft)
                briefing.store.import_report(packet,'启动验证（模拟资料）','【当前建议】观望\n【主要原因】模拟测试。\n【行动条件】核实数据后再评估。\n【主要风险】资料不确定。\n【数据截至】模拟行情\n【操作思路】全部保留，核实资料后再评估。',now=now)
                briefing.refresh_reports();app.processEvents()
                result['concise_report_visible'] = ('【当前建议】观望' in briefing.suggestionText.toPlainText()
                    and '【操作思路】全部保留' in briefing.suggestionText.toPlainText() and not briefing.reportDetails.isVisible())
                app.portfolio.quotes['001330.SZ']['current_price']=11;app.portfolio.render()
                result['changed_snapshot_report_warning'] = '数据已变化' in briefing.reportStateLabel.text()
                briefing.tabs.setCurrentIndex(4)
                briefing.candidateNameEdit.setText('浦发银行');briefing.candidateCodeEdit.setText('600000.SH');briefing.add_candidate()
                briefing.candidatesTable.setCurrentCell(0,0);briefing.analyze_candidate();app.processEvents()
                result['independent_candidate_ready'] = (briefing.tabs.currentIndex()==2 and '未登记持仓' in briefing.promptText.toPlainText()
                    and '9.429' not in briefing.promptText.toPlainText() and len(app.portfolio.store.load_all())==1)
                briefing.compare_candidates();app.processEvents()
                result['comparison_ready'] = len(briefing.current_positions())==2 and '支持证据和反对证据' in briefing.promptText.toPlainText()
                briefing.discover();app.processEvents()
                result['discovery_prompt_ready'] = '不得只分析我当前持仓' in briefing.promptText.toPlainText()
                from stockwidget.core.analysis_prompt import research_instructions,TEMPLATE_ID
                result['analysis_template']=TEMPLATE_ID
                result['unified_prompt_all_modes']=True
                for mode in (0,1,2):
                    briefing.taskCombo.setCurrentIndex(mode);briefing.rebuild();app.processEvents()
                    prefix,payload=briefing.promptText.toPlainText().split('引用资料JSON：\n',1)
                    result['unified_prompt_all_modes'] &= (prefix+'引用资料JSON：\n'==research_instructions(True)
                        and '最多3至5只' not in prefix and bool(json.loads(payload)['资料行']))
                result['ui_scale'] = app.devicePixelRatio()
                result["ok"] = (result["panel_visible"] and result["icu_from_system32"]
                                and result["reminder_settings_visible"] and result["risk_database_ready"]
                                and result["spin_arrow_increments"] and result["news_center_visible"]
                                and result["overview_ready"] and result["chatgpt_prompt_private_default_off"]
                                and result["ai_history_visible"] and result['synthetic_snapshot_export_matches_home']
                                and result['concise_report_visible'] and result['changed_snapshot_report_warning']
                                and result['independent_candidate_ready'] and result['comparison_ready'] and result['discovery_prompt_ready']
                                and result['unified_prompt_all_modes'])
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
