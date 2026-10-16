"""持仓面板交互、行情隔离与锁定仓二次确认。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import threading
import unittest

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import delete

import resources.resources_rc  # noqa: F401
from stockwidget.core.portfolio import PositionStore
from stockwidget.ui.portfolio import PortfolioPanel, QuotePoller

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
        self.warning = self.enterContext(patch("stockwidget.ui.portfolio.QMessageBox.warning"))
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

    def quote(self, **values):
        data = dict(current_price=10, prev_close=9, date="2020-01-01", time="15:00:00")
        data.update(values)
        self.panel.accept_quote(("sz001330", "sina", self.panel.generation, True, {"sz001330": data}, None))

    def test_offline_panel_opens_without_fake_price_or_ai(self):
        self.assertEqual(self.panel.priceLabel.text(), "暂无行情")
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
        self.assertIn("+0.00", self.panel.profitLabel.text())

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
        with patch("stockwidget.ui.portfolio.QMessageBox.question", return_value=QMessageBox.No) as question:
            self.button("saveLockedButton").click()
        question.assert_called_once()
        self.assertEqual(self.store.load().locked, 200)

    def test_lock_page_yes_confirmation_saves(self):
        self.button("settingsButton").click()
        self.panel.lockedEdit.setText("100")
        with patch("stockwidget.ui.portfolio.QMessageBox.question", return_value=QMessageBox.Yes):
            self.button("saveLockedButton").click()
        self.assertEqual(self.store.load().locked, 100)

    def test_quote_failure_preserves_database_and_retains_old_time(self):
        self.quote()
        before = self.store.path.read_bytes()
        old_time = self.panel.timeLabel.text()
        self.panel.accept_quote(("sz001330", "sina", self.panel.generation, False, None, "网络异常"))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.panel.timeLabel.text(), old_time)
        self.assertIn("显示上次行情", self.panel.statusLabel.text())
        self.assertIn("已过期", self.panel.timeLabel.text())

    def test_stale_symbol_response_cannot_value_new_position(self):
        self.button("editButton").click()
        self.panel.codeEdit.setText("600000.SH")
        self.panel.nameEdit.setText("浦发银行")
        self.button("savePositionButton").click()
        self.panel.accept_quote(("sz001330", "sina", 0, True, {"sz001330": {"current_price": 100}}, None))
        self.assertIsNone(self.panel.quote)
        self.assertEqual(self.panel.priceLabel.text(), "暂无行情")

    def test_source_switch_clears_previous_source_quote(self):
        self.quote()
        self.app.win.data_source = "eastmoney"
        self.app.win.configuration_changed.emit()
        self.assertEqual(self.panel.priceLabel.text(), "暂无行情")
        self.assertIn("东方财富", self.panel.sourceLabel.text())

    def test_invalid_quote_does_not_generate_valuation(self):
        self.quote(current_price=0)
        self.assertEqual(self.panel.priceLabel.text(), "暂无行情")
        self.assertIn("行情更新失败", self.panel.statusLabel.text())

    def test_missing_time_is_never_reported_as_fresh(self):
        self.quote(date="", time="")
        self.assertIn("无法判断", self.panel.timeLabel.text())

    def test_zero_cost_ui_and_red_up_green_down(self):
        self.quote()
        self.assertIn("#ff7878", self.panel.changeLabel.styleSheet())
        self.quote(current_price=8)
        self.assertIn("#61ce9c", self.panel.profitLabel.styleSheet())

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
