"""持仓面板交互、行情隔离与锁定仓二次确认。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import threading
import unittest

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QVBoxLayout
from PySide6.QtGui import QPalette
from shiboken6 import delete

import resources.resources_rc  # noqa: F401
from stockwidget.core.portfolio import PositionStore
from stockwidget.ui.portfolio import PortfolioPanel, QuotePoller
from stockwidget.ui.controls.style import apply_portfolio_theme, theme_palette

REAL_FETCH = QuotePoller.fetch


class QuoteSettings(QObject):
    configuration_changed = Signal()

    def __init__(self):
        super().__init__()
        self.data_source = "sina"
        self.refresh_seconds = 60
        self.watchlist = {}
        self.widget_visible = True
        self.set_watchlist = Mock()
        self.raise_ = Mock()
        self.set_refresh_interval = Mock()
        self.set_data_source = Mock()


class PortfolioPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.store = PositionStore(Path(directory) / "positions.sqlite3")
        self.fetch_patch = patch.object(QuotePoller, "fetch")
        self.fetch = self.enterContext(self.fetch_patch)
        self.warning = self.enterContext(patch.object(PortfolioPanel, "_error"))
        self.question = self.enterContext(patch.object(PortfolioPanel, "_question", return_value=True))
        self.app = SimpleNamespace(win=QuoteSettings(), open_settings=Mock())
        self.panel = PortfolioPanel(self.store, self.app)
        self.panel.show()
        self.qt.processEvents()

    def tearDown(self):
        self.panel.timer.stop()
        self.panel.clock.stop()
        delete(self.panel)

    def button(self, name):
        return self.panel.form.findChild(QObject, name)

    def cell(self, column, row=0):
        return self.panel.positionsTable.item(row, column)

    def quote(self, **values):
        data = dict(current_price=10, prev_close=9, date="2020-01-01", time="15:00:00")
        data.update(values)
        self.panel.accept_quote(("sz001330", "sina", self.panel.generation, True, {"sz001330": data}, None))

    def test_offline_panel_opens_without_fake_price_or_ai(self):
        self.assertEqual(self.cell(1).text(), "暂无行情")
        self.assertIn("尚未启用", self.button("aiLabel").text())
        self.assertIn("未连接券商", self.button("localLabel").text())
        self.assertEqual(self.panel.timer.interval(), 60000)

    def test_edit_button_save_and_restart(self):
        self.button("editButton").click()
        self.panel.totalEdit.setText("1300")
        self.panel.costEdit.setText("10")
        self.button("savePositionButton").click()
        self.assertEqual(PositionStore(self.store.path).load().total, 1300)
        self.quote()
        self.assertIn("+0.00", self.cell(3).text())

    def test_cancel_edit_has_no_database_write(self):
        before = self.store.path.read_bytes()
        self.button("editButton").click()
        self.panel.totalEdit.setText("1400")
        self.button("cancelEditButton").click()
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_cannot_reduce_total_below_locked_in_editor(self):
        self.button("editButton").click()
        self.panel.totalEdit.setText("100")
        self.button("savePositionButton").click()
        self.assertEqual(self.store.load().total, 1200)
        self.warning.assert_called_once()

    def test_lock_page_no_confirmation_preserves_data(self):
        self.button("settingsButton").click()
        self.assertEqual(self.panel.pages.currentIndex(), 2)
        self.panel.lockedEdit.setText("100")
        with patch.object(self.panel, "_question", return_value=False) as question:
            self.button("saveLockedButton").click()
        question.assert_called_once()
        self.assertEqual(self.store.load().locked, 200)

    def test_lock_page_yes_confirmation_saves(self):
        self.button("settingsButton").click()
        self.panel.lockedEdit.setText("100")
        with patch.object(self.panel, "_question", return_value=True):
            self.button("saveLockedButton").click()
        self.assertEqual(self.store.load().locked, 100)

    def test_quote_failure_preserves_database_and_retains_old_time(self):
        self.quote()
        before = self.store.path.read_bytes()
        old_time = self.cell(5).text().splitlines()[:2]
        self.panel.accept_quote(("sz001330", "sina", self.panel.generation, False, None, "网络异常"))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.cell(5).text().splitlines()[:2], old_time)
        self.assertIn("显示上次行情", self.cell(5).text())
        self.assertIn("已过期", self.cell(5).text())

    def test_stale_symbol_response_cannot_value_new_position(self):
        self.button("editButton").click()
        self.panel.codeEdit.setText("600000.SH")
        self.panel.nameEdit.setText("浦发银行")
        self.button("savePositionButton").click()
        self.panel.accept_quote(("sz001330", "sina", 0, True, {"sz001330": {"current_price": 100}}, None))
        self.assertEqual(self.panel.quotes, {})
        self.assertEqual(self.cell(1).text(), "暂无行情")

    def test_source_switch_clears_previous_source_quote(self):
        self.quote()
        self.app.win.data_source = "eastmoney"
        self.app.win.configuration_changed.emit()
        self.assertEqual(self.cell(1).text(), "暂无行情")
        self.assertIn("东方财富", self.panel.sourceLabel.text())

    def test_invalid_quote_does_not_generate_valuation(self):
        self.quote(current_price=0)
        self.assertEqual(self.cell(1).text(), "暂无行情")
        self.assertIn("行情更新失败", self.cell(5).text())

    def test_missing_time_is_never_reported_as_fresh(self):
        self.quote(date="", time="")
        self.assertIn("无法判断", self.cell(5).text())

    def test_zero_cost_ui_and_red_up_green_down(self):
        self.quote()
        self.assertEqual(self.cell(1).foreground().color().name(), "#ff7878")
        self.quote(current_price=8)
        self.assertEqual(self.cell(3).foreground().color().name(), "#61ce9c")

    def test_network_payload_excludes_personal_position(self):
        parent = QObject()
        poller = QuotePoller(parent)
        done = threading.Event()
        result = []
        def fake_fetch(instruments, source):
            result.append(instruments)
            done.set()
            return False, None, "离线"
        with patch("stockwidget.ui.portfolio.fetch_quote_result", side_effect=fake_fetch):
            REAL_FETCH(poller, self.store.load().instrument(), "sina", 0)
            self.assertTrue(done.wait(2))
        self.assertEqual(set(result[0]["sz001330"]), {"code", "market", "name", "type", "checked"})
        delete(parent)

    def add_second(self):
        p = self.store.add_position(name="浦发银行",code="600000.SH",total=500,cost="10")
        self.panel._reload()
        self.panel.render()
        return p

    def test_add_button_saves_second_stock_with_zero_lock(self):
        self.button("addButton").click()
        for widget,value in ((self.panel.nameEdit,"浦发银行"),(self.panel.codeEdit,"600000.SH"),
                             (self.panel.totalEdit,"500"),(self.panel.costEdit,"10")):
            widget.setText(value)
        self.button("savePositionButton").click()
        self.assertEqual(self.panel.positionsTable.rowCount(),2)
        self.assertEqual(self.store.load_all()[1].locked,0)

    def test_cancel_add_confirmation_does_not_save(self):
        self.button("addButton").click()
        self.panel.nameEdit.setText("浦发银行")
        self.panel.codeEdit.setText("600000.SH")
        self.panel.totalEdit.setText("100")
        self.panel.costEdit.setText("10")
        self.question.return_value=False
        self.button("savePositionButton").click()
        self.assertEqual(len(self.store.load_all()),1)

    def test_select_second_edit_does_not_change_first(self):
        p=self.add_second()
        self.panel.positionsTable.setCurrentCell(1,0)
        self.button("editButton").click()
        self.panel.totalEdit.setText("700")
        self.button("savePositionButton").click()
        self.assertEqual(self.store.load(p.position_id).total,700)
        self.assertEqual(self.store.load().total,1200)

    def test_archive_requires_confirmation_and_preserves_other_row(self):
        p=self.add_second()
        self.panel.positionsTable.setCurrentCell(1,0)
        self.question.return_value=False
        self.button("archiveButton").click()
        self.assertEqual(len(self.store.load_all()),2)
        self.question.return_value=True
        self.button("archiveButton").click()
        self.assertEqual(self.store.load().locked,200)
        self.assertTrue(self.store.load(p.position_id,include_archived=True).archived)

    def test_partial_quote_coverage_is_explicit(self):
        self.add_second()
        self.quote()
        self.assertIn("1/2",self.panel.coverageLabel.text())
        self.assertIn("数据不完整",self.panel.coverageLabel.text())
        self.assertIn("已覆盖部分",self.panel.summaryValueLabel.text())
        self.assertEqual(self.cell(1,1).text(),"暂无行情")

    def test_refresh_batches_both_stocks_without_position_values(self):
        self.add_second()
        self.fetch.reset_mock()
        self.panel.refresh()
        self.fetch.assert_called_once()
        instruments=self.fetch.call_args.args[0]
        self.assertEqual(set(instruments),{"sz001330","sh600000"})
        self.assertFalse(any('cost' in p or 'total' in p or 'locked' in p for p in instruments.values()))

    def test_archived_last_stock_does_not_reseed_and_can_restore(self):
        self.button("archiveButton").click()
        self.assertEqual(self.panel.positionsTable.rowCount(),0)
        self.button("settingsButton").click()
        self.button("restoreButton").click()
        self.assertEqual(self.panel.positionsTable.rowCount(),1)
        self.assertEqual(self.store.load().locked,200)

    def test_many_rows_scroll_instead_of_growing_window(self):
        initial_height=self.panel.height()
        for i in range(10):
            self.store.add_position(name=f"测试{i}",code=f"600{i:03d}.SH",total=100,cost=10)
        self.panel._reload()
        self.panel.render()
        self.qt.processEvents()
        self.assertEqual(self.panel.height(),initial_height)
        self.assertGreater(self.panel.positionsTable.verticalScrollBar().maximum(),0)

    def test_dark_inputs_contrast_under_light_and_dark_application_palette(self):
        original=self.qt.palette()
        def luminance(color):
            values=[v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4 for v in (color.redF(),color.greenF(),color.blueF())]
            return sum(v*k for v,k in zip(values,(0.2126,0.7152,0.0722)))
        try:
            for dark in (False,True):
                self.qt.setPalette(theme_palette(dark,QPalette()))
                self.button("editButton").click()
                self.qt.processEvents()
                p=self.panel.nameEdit.palette()
                background=p.color(QPalette.Base)
                text=p.color(QPalette.Text)
                contrast=(luminance(text)+0.05)/(luminance(background)+0.05)
                self.assertLess(background.lightness(),90)
                self.assertGreaterEqual(contrast,7)
                self.assertGreater(p.color(QPalette.PlaceholderText).lightness(),120)
        finally:
            self.qt.setPalette(original)

    def test_scoped_dialog_spinbox_combo_and_disabled_palette(self):
        original=self.qt.palette()
        dialog=QDialog(self.panel)
        layout=QVBoxLayout(dialog)
        controls=[QLineEdit(),QSpinBox(),QDoubleSpinBox(),QComboBox()]
        for control in controls:
            layout.addWidget(control)
        controls[-1].addItems(["新浪财经","东方财富"])
        apply_portfolio_theme(dialog)
        dialog.show()
        self.qt.processEvents()
        for control in controls:
            self.assertLess(control.palette().color(QPalette.Base).lightness(),100)
            self.assertGreater(control.palette().color(QPalette.Text).lightness(),180)
        controls[0].setEnabled(False)
        self.assertGreater(controls[0].palette().color(QPalette.Disabled,QPalette.Text).lightness(),120)
        self.assertEqual(self.qt.palette(),original)
        dialog.close()
        delete(dialog)
