"""行情到剪贴板、人工报告与最新持仓的端到端界面回归（临时数据）。"""
import json
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from stockwidget.core.briefing import BriefingStore
from tests.ui import test_briefing as fixture
from tests.core import test_stage4 as sample


class Stage4UiTests(unittest.TestCase):
    setUp=fixture.BriefingUiTests.setUp
    tearDown=fixture.BriefingUiTests.tearDown
    setUpClass=classmethod(fixture.BriefingUiTests.setUpClass.__func__)
    open=fixture.BriefingUiTests.open
    add_second=fixture.BriefingUiTests.add_second
    button=fixture.BriefingUiTests.button
    def accept(self,data,ok=True,error=None):
        self.panel.accept_quote(('',self.panel.source,self.panel.generation,ok,data,error))
    def quote(self,price=6.34,**kw):
        return dict(current_price=price,prev_close=6.68,date='2026-10-19',time='15:35:15',
                    _price_kind='close',_close_date='2026-10-19',**kw)
    def imported(self,text=sample.ANSWER):
        w=self.open(3);w.copy_prompt();w.importText.setPlainText(text);w.import_report();return w
    def test_first_offline_copy_has_no_fabricated_value(self):
        w=self.open(2);w.privateCheck.setChecked(True);w.copy_prompt()
        text=QApplication.clipboard().text()
        self.assertIn('暂无可用行情',text);self.assertNotIn('参考市值',text)
        self.assertIn('暂无行情',self.panel.positionsTable.item(0,1).text())
    def test_restart_panel_reads_last_valid_snapshot_and_privacy_preference(self):
        from shiboken6 import delete
        from stockwidget.ui.portfolio import PortfolioPanel
        self.accept({'sz001330':self.quote()});w=self.open(2);w.privateCheck.setChecked(True)
        self.panel.timer.stop();self.panel.clock.stop();delete(self.panel)
        self.panel=PortfolioPanel(self.store,self.app);self.panel.show();self.qt.processEvents()
        w=self.open(2);self.assertTrue(w.privateCheck.isChecked())
        self.assertIn('6.34',self.panel.positionsTable.item(0,1).text())
        self.assertIn('-3706.80',w.promptText.toPlainText());self.assertEqual(self.store.load().locked,200)
    def test_two_stocks_match_home_and_export_snapshot(self):
        second=self.add_second()
        self.accept({'sz001330':self.quote(),'sh600000':self.quote(11.12)})
        w=self.open(2);w.scopeCombo.setCurrentIndex(0);w.privateCheck.setChecked(True);w.copy_prompt()
        text=QApplication.clipboard().text();self.assertIn('6.34',text);self.assertIn('11.12',text)
        self.assertIn('6.34',self.panel.positionsTable.item(0,1).text())
        self.assertIn('11.12',self.panel.positionsTable.item(1,1).text())
        self.assertIn('2026-10-19 15:35:15',text);self.assertIn('非实时',text)
        self.assertEqual({p['code'] for p in w.draft['scope']},{sample.CODE,second.code})
    def test_failure_keeps_price_error_and_persistent_cache_without_hold_change(self):
        self.accept({'sz001330':self.quote()});before=self.store.path.read_bytes()
        self.accept({},False,'网络超时')
        w=self.open(2);w.privateCheck.setChecked(True);w.copy_prompt()
        text=QApplication.clipboard().text()
        for value in ('6.34','-3706.80','网络超时','2026-10-19'):self.assertIn(value,text)
        reopened=BriefingStore(w.store.path);quote=reopened.load_quotes('sina')[sample.CODE]
        self.assertEqual(quote['current_price'],6.34);self.assertEqual(quote['_last_error'],'网络超时')
        self.assertEqual(self.store.path.read_bytes(),before)
    def test_mismatched_code_and_older_quote_do_not_overwrite_price(self):
        self.accept({'sz001330':self.quote()})
        self.accept({'sz001330':self.quote(99,_code='600000.SH')})
        self.assertEqual(self.panel.quotes[sample.CODE]['current_price'],6.34)
        self.assertIn('不匹配',self.panel.quote_errors[sample.CODE])
        old=self.quote(1);old['date']='2026-10-16'
        self.accept({'sz001330':old})
        self.assertEqual(self.panel.quotes[sample.CODE]['current_price'],6.34)
    def test_copy_reads_saved_changes_and_remembers_then_disables_privacy(self):
        w=self.open(2);w.privateCheck.setChecked(True);p=self.store.load()
        self.store.update_position(name=p.name,code=p.code,total=1300,cost='8.123',expected_version=p.version)
        w.copy_prompt();text=QApplication.clipboard().text()
        for value in ('1300 股','8.123','锁定仓 200 股','策略仓 1100 股'):self.assertIn(value,text)
        self.assertIn('已包含',w.privacyStateLabel.text())
        self.assertTrue(BriefingStore(w.store.path).private_preference())
        w.privateCheck.setChecked(False);w.copy_prompt()
        self.assertIn('未包含',w.privacyStateLabel.text());self.assertNotIn('8.123',QApplication.clipboard().text())
        self.assertFalse(BriefingStore(w.store.path).private_preference())
    def test_summary_first_raw_expands_and_stays_unchanged(self):
        w=self.imported()
        self.assertIn('【当前建议】观望',w.suggestionText.toPlainText())
        self.assertNotIn('原始正文',w.suggestionText.toPlainText());self.assertFalse(w.reportDetails.isVisible())
        w.expandReportCheck.setChecked(True);self.qt.processEvents()
        self.assertTrue(w.reportDetails.isVisible());self.assertIn(sample.ANSWER,w.reportDetails.toPlainText())
        self.assertEqual(w.store.reports()[0]['content'],sample.ANSWER)
    def test_ordinary_text_falls_back_without_inventing_conclusion(self):
        content='普通回答：资料不够。\n<script>不是代码</script>'
        w=self.imported(content)
        self.assertIn(content,w.suggestionText.toPlainText());self.assertTrue(w.reportDetails.isVisible())
        self.assertEqual(w.store.reports()[0]['content'],content)
    def test_report_keeps_original_stock_scope_when_selection_changes(self):
        second=self.add_second();w=self.imported()
        w.scopeCombo.setCurrentIndex(2);w.refresh_report_status()
        self.assertIn('原报告范围：001330.SZ',w.reportStateLabel.text())
        self.assertEqual([p['code'] for p in json.loads(w.store.reports()[0]['scope_json'])],[sample.CODE])
        self.assertNotIn(second.code,w.reportStateLabel.text())
    def test_report_quote_and_hold_changes_mark_stale_without_rewriting(self):
        self.accept({'sz001330':self.quote()});w=self.imported();original=w.store.reports()[0]['content']
        self.accept({'sz001330':self.quote(6.35)})
        self.assertIn('数据已变化',w.reportStateLabel.text())
        w.copy_prompt();w.importText.setPlainText(sample.ANSWER);w.import_report()
        p=self.store.load();self.store.update_position(name=p.name,code=p.code,total=1300,cost=p.cost,expected_version=p.version)
        self.panel._reload();self.panel.render()
        self.assertIn('数据已变化',w.reportStateLabel.text());self.assertEqual(w.store.reports()[0]['content'],original)
    def test_failed_daily_source_preserves_reference_and_does_not_fake_close(self):
        from stockwidget.data.closing_quotes import ClosingQuotes
        import requests
        raw={'sz001330':dict(current_price=6.34,date='2026-10-19',time='14:00:00')}
        with patch('stockwidget.data.closing_quotes.request_sina_bars',side_effect=requests.Timeout):
            output=ClosingQuotes().enrich({'sz001330':self.store.load().instrument()},'sina',raw,now=sample.NOW)
        self.assertEqual(output,raw);self.assertNotIn('_price_kind',output['sz001330'])
    def test_repeated_manual_refresh_does_not_queue_network_requests(self):
        import threading,time
        from tests.ui.test_portfolio import REAL_FETCH
        from stockwidget.ui.portfolio import QuotePoller
        from PySide6.QtCore import QObject
        from shiboken6 import delete
        parent=QObject();poller=QuotePoller(parent);started=threading.Event();release=threading.Event();finished=[]
        poller.finished.connect(lambda result:finished.append(result))
        def blocked(*args):started.set();release.wait(3);return False,None,'模拟断网'
        try:
            with patch('stockwidget.ui.portfolio.fetch_quote_result',side_effect=blocked) as request:
                self.assertTrue(REAL_FETCH(poller,self.store.load().instrument(),'sina',1))
                self.assertTrue(started.wait(1))
                self.assertFalse(REAL_FETCH(poller,self.store.load().instrument(),'sina',1))
                request.assert_called_once();release.set()
                deadline=time.monotonic()+3
                while not finished and time.monotonic()<deadline:self.qt.processEvents();time.sleep(.005)
                self.assertEqual(len(finished),1)
        finally:release.set();delete(parent)
