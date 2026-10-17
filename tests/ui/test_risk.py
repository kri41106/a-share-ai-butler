"""提醒配置、通知回退、股票身份与持仓隔离的界面行为。"""
from pathlib import Path
from unittest.mock import Mock, patch
import time
import unittest
from PySide6.QtWidgets import QApplication
from shiboken6 import delete
from stockwidget.core.risk import RiskError, RiskStore
from stockwidget.ui.risk import RiskController
from tests.ui import test_portfolio as portfolio_tests


class RiskUiTests(unittest.TestCase):
    setUp = portfolio_tests.PortfolioPanelTests.setUp
    tearDown = portfolio_tests.PortfolioPanelTests.tearDown
    button = portfolio_tests.PortfolioPanelTests.button
    cell = portfolio_tests.PortfolioPanelTests.cell
    add_second = portfolio_tests.PortfolioPanelTests.add_second

    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def open(self,index=0):
        center = self.panel.risk.open(index)
        self.qt.processEvents()
        return center

    def fresh(self,price=10):
        self.panel.accept_quote(('sz001330','sina',self.panel.generation,True,
                                 {'sz001330':{'current_price':price,'prev_close':10,'timestamp':time.time()}},None))

    def test_home_buttons_open_analysis_settings_and_records(self):
        for name,index in [('riskButton',0),('alertsButton',1),('recordsButton',2)]:
            self.button(name).click()
            center=self.panel.risk.window
            self.assertTrue(center.isVisible())
            self.assertEqual(center.tabs.currentIndex(),index)
            self.assertIn('001330.SZ',center.stockLabel.text())

    def test_enabled_condition_requires_value_and_does_not_change_holdings(self):
        center=self.open(1)
        before=self.store.load()
        center.aboveEnabled.setChecked(True)
        center.saveAlertsButton.click()
        self.assertIn('阈值',center.errorLabel.text())
        self.assertFalse(self.panel.risk.store.load_settings(1,before.code)['above_enabled'])
        self.assertEqual(self.store.load(),before)

    def test_save_settings_only_for_selected_stock_no_network_request(self):
        second=self.add_second()
        self.panel.positionsTable.setCurrentCell(1,0)
        self.fetch.reset_mock()
        center=self.open(1)
        center.belowEnabled.setChecked(True)
        center.belowEdit.setText('8.12')
        center.saveAlertsButton.click()
        self.assertTrue(self.panel.risk.store.load_settings(second.position_id,second.code)['below_enabled'])
        self.assertFalse(self.panel.risk.store.load_settings(1,'001330.SZ')['below_enabled'])
        self.assertEqual(self.store.load().locked,200)
        self.fetch.assert_not_called()

    def test_desktop_notice_contains_identity_condition_price_source_and_time(self):
        self.panel.app.tray=Mock()
        self.panel.app.tray.isVisible.return_value=True
        self.panel.risk.store.save_settings(1,'001330.SZ',{'above_enabled':True,'above':'9'})
        with patch('stockwidget.ui.risk.QSystemTrayIcon.supportsMessages',return_value=True):
            self.fresh(10)
            self.fresh(11)
        self.panel.app.tray.showMessage.assert_called_once()
        title,body,*_=self.panel.app.tray.showMessage.call_args.args
        for text in ('博纳影业','001330.SZ'):
            self.assertIn(text,title)
        for text in ('股价高于9元','10元','新浪财经','北京时间','仅供参考'):
            self.assertIn(text,body)
        self.assertNotIn('9.429',body)

    def test_system_notice_unavailable_preserves_record_and_read_status(self):
        self.panel.app.tray=Mock()
        self.panel.risk.store.save_settings(1,'001330.SZ',{'below_enabled':True,'below':'11'})
        with patch('stockwidget.ui.risk.QSystemTrayIcon.supportsMessages',return_value=False):
            self.fresh(10)
        self.panel.app.tray.showMessage.assert_not_called()
        center=self.open(2)
        self.assertEqual(center.recordsTable.rowCount(),1)
        self.assertIn('股价低于',center.recordDetails.toPlainText())
        center.markReadButton.click()
        self.assertEqual(center.recordsTable.item(0,4).text(),'已读')
        reopened=RiskStore(self.panel.risk.store.path)
        self.assertEqual(reopened.records()[0]['is_read'],1)

    def test_notification_exception_does_not_drop_history_or_mutate_position(self):
        self.panel.app.tray=Mock()
        self.panel.app.tray.isVisible.return_value=True
        self.panel.app.tray.showMessage.side_effect=RuntimeError('unavailable')
        self.panel.risk.store.save_settings(1,'001330.SZ',{'above_enabled':True,'above':9})
        before=self.store.load()
        with patch('stockwidget.ui.risk.QSystemTrayIcon.supportsMessages',return_value=True):
            self.fresh()
        self.assertEqual(len(self.panel.risk.store.records()),1)
        self.assertEqual(self.store.load(),before)

    def test_failed_batch_counts_each_stock_and_preserves_holdings(self):
        second=self.add_second()
        for p in self.store.load_all():
            self.panel.risk.store.save_settings(p.position_id,p.code,{'data_enabled':True,'failures':2})
        before=self.store.path.read_bytes()
        for _ in range(4):
            self.panel.accept_quote((('sz001330','sh600000'),'sina',self.panel.generation,False,None,'offline'))
        self.assertEqual(self.store.path.read_bytes(),before)
        records=self.panel.risk.store.records()
        self.assertEqual({r['code'] for r in records},{'001330.SZ',second.code})
        self.assertEqual(len(records),2)

    def test_archive_disables_monitoring_and_all_history_remains_visible(self):
        self.panel.risk.store.save_settings(1,'001330.SZ',{'above_enabled':True,'above':9})
        self.fresh()
        self.panel.archive_position()
        self.panel.risk.evaluate()
        center=self.open(2)
        self.assertEqual(center.recordsTable.rowCount(),1)
        self.assertFalse(center.saveAlertsButton.isEnabled())
        self.assertEqual(len(self.panel.risk.store.records()),1)

    def test_changed_stock_code_rejects_stale_settings_window(self):
        center=self.open(1)
        self.store.update_position(name='新股票',code='600000.SH',total=1200,cost=10,expected_version=1)
        center.aboveEnabled.setChecked(True)
        center.aboveEdit.setText('9')
        center.save()
        self.assertIn('代码已更改',center.errorLabel.text())
        self.assertFalse(self.panel.risk.store.load_settings(1,'600000.SH')['above_enabled'])

    def test_analysis_record_and_mark_read_do_not_execute_position_changes(self):
        center=self.open()
        self.fresh(8)
        before=self.store.path.read_bytes()
        center.analyzeButton.click()
        self.assertIn('非AI模型',center.analysisText.toPlainText())
        center.recordMode.setCurrentIndex(1)
        center.tabs.setCurrentIndex(2)
        self.assertGreater(center.recordsTable.rowCount(),0)
        self.assertFalse(center.markReadButton.isEnabled())
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_risk_database_failure_keeps_holdings_available(self):
        with patch('stockwidget.ui.risk.RiskStore',side_effect=RiskError('模拟提醒库不可用')):
            controller=RiskController(self.panel)
            self.assertIsNone(controller.store)
            window=controller.open(1)
            self.assertIn('提醒库不可用',window.errorLabel.text())
            self.assertFalse(window.saveAlertsButton.isEnabled())
            self.assertEqual(self.store.load().locked,200)
            window.close()
            delete(window)
            delete(controller)

    def test_source_change_resets_failure_counters_and_rejects_old_response(self):
        self.panel.risk.failures[(1,'001330.SZ')]=3
        self.panel.app.win.data_source='eastmoney'
        self.panel.sync_quote_settings()
        self.assertFalse(self.panel.risk.failures)
        self.panel.accept_quote(('sz001330','sina',self.panel.generation-1,True,{},None))
        self.assertFalse(self.panel.risk.failures)
