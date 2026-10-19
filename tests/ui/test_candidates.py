"""候选研究和持仓比较的桌面行为，所有网络用模拟来源。"""
import time
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from tests.ui import test_briefing as fixture
from tests.core.test_stage4 import ANSWER


class CandidateUiTests(unittest.TestCase):
    setUp=fixture.BriefingUiTests.setUp
    tearDown=fixture.BriefingUiTests.tearDown
    setUpClass=classmethod(fixture.BriefingUiTests.setUpClass.__func__)
    open=fixture.BriefingUiTests.open
    wait=fixture.BriefingUiTests.wait
    button=fixture.BriefingUiTests.button
    def add(self):
        w=self.open(4);w.candidateNameEdit.setText('浦发银行');w.candidateCodeEdit.setText('600000.SH');w.add_candidate();return w
    def test_home_opens_other_stock_research_without_browser_or_upload(self):
        with patch('stockwidget.ui.briefing.QDesktopServices.openUrl') as browser:
            self.button('candidatesButton').click();self.qt.processEvents()
            self.assertEqual(self.panel.briefing.tabs.currentIndex(),4);browser.assert_not_called()
    def test_add_and_analyze_candidate_without_modifying_holdings(self):
        before=self.store.path.read_bytes();w=self.add();w.candidatesTable.setCurrentCell(0,0);w.analyze_candidate()
        self.assertEqual([p.code for p in w.current_positions()],['600000.SH']);self.assertEqual(w.tabs.currentIndex(),2)
        w.privateCheck.setChecked(True);w.copy_prompt();text=QApplication.clipboard().text()
        self.assertIn('未登记持仓',text);self.assertNotIn('9.429',text);self.assertNotIn('1200 股',text)
        self.assertIn('没有登记持仓可包含',w.privacyStateLabel.text());self.assertEqual(self.store.path.read_bytes(),before)
    def test_discovery_requests_other_stocks_and_comparison_scopes_all(self):
        w=self.add();w.discover()
        self.assertEqual(w.scopeCombo.currentData(),'combined');self.assertIn('不得只分析我当前持仓',w.promptText.toPlainText())
        w.compare_candidates();self.assertEqual(w.taskCombo.currentIndex(),2)
        self.assertEqual({p.code for p in w.current_positions()},{'001330.SZ','600000.SH'})
        self.assertIn('支持证据和反对证据',w.promptText.toPlainText())
    def test_failed_candidate_quotes_retain_price_and_do_not_change_holdings(self):
        w=self.add();before=self.store.path.read_bytes();keys=('sh600000',)
        data={'sh600000':dict(current_price=9.78,prev_close=9.73,timestamp=time.time())}
        w.accept_candidate_quotes((keys,'sina',self.panel.generation,True,data,None))
        w.accept_candidate_quotes((keys,'sina',self.panel.generation,False,None,'模拟网络超时'))
        w.candidatesTable.setCurrentCell(0,0);w.analyze_candidate();w.copy_prompt()
        text=QApplication.clipboard().text();self.assertIn('9.78',text);self.assertIn('模拟网络超时',text)
        self.assertEqual(self.store.path.read_bytes(),before)
    def test_source_generation_code_and_backwards_time_do_not_cross_match(self):
        w=self.add();keys=('sh600000',);gen=self.panel.generation;now=time.time()
        w.accept_candidate_quotes((keys,'sina',gen,True,{'sh600000':dict(current_price=10,timestamp=now)},None))
        for source,generation,q in [('eastmoney',gen,dict(current_price=99,timestamp=now)),('sina',gen-1,dict(current_price=99,timestamp=now)),('sina',gen,dict(current_price=99,_code='001330.SZ',timestamp=now)),('sina',gen,dict(current_price=99,timestamp=now-100))]:
            w.accept_candidate_quotes((keys,source,generation,True,{'sh600000':q},None))
        self.assertEqual(w.candidate_quotes['600000.SH']['current_price'],10)
    def test_archiving_requires_confirmation_and_preserves_report_raw(self):
        w=self.add();w.candidatesTable.setCurrentCell(0,0);w.analyze_candidate();w.copy_prompt()
        w.importText.setPlainText(ANSWER);w.import_report();self.question.return_value=False
        w.candidatesTable.setCurrentCell(0,0);w.archive_candidate();self.assertEqual(len(w.candidates.load_all()),1)
        self.question.return_value=True;w.archive_candidate();self.assertFalse(w.candidates.load_all())
        self.assertEqual(w.store.reports()[0]['content'],ANSWER);self.assertIn('数据已变化',w.reportStateLabel.text())
        self.assertEqual(self.store.load().locked,200)
    def test_candidate_news_fetch_only_public_code_and_cache_stock_bound(self):
        from tests.core.test_briefing import row
        w=self.add();w.candidatesTable.setCurrentCell(0,0);w.analyze_candidate()
        with patch('stockwidget.ui.briefing.fetch_announcements',side_effect=lambda code:[row(code)]) as announcements,patch('stockwidget.ui.briefing.fetch_news',return_value=[]):
            w.fetch(True);self.wait(w)
        announcements.assert_called_once_with('600000.SH');self.assertEqual(len(w.store.cached('600000.SH','announcement')[0]),1)
        self.assertEqual(w.store.cached('001330.SZ','announcement')[0],[])
    def test_candidate_quote_request_has_no_private_values_and_no_duplicate_hold_request(self):
        w=self.add();w.tabs.setCurrentIndex(4);self.fetch.reset_mock();w.refresh_quotes()
        args=self.fetch.call_args.args
        self.assertEqual(set(args[0]),{'sh600000'});self.assertEqual(set(args[0]['sh600000']),{'market','code','type','name','checked'})
        self.assertEqual(self.store.load().locked,200)
    def test_discovery_report_is_not_presented_as_recommendation_for_bona(self):
        w=self.add();w.discover();w.copy_prompt();w.importText.setPlainText('尚未核实其他候选');w.import_report()
        self.assertIn('全A股候选研究',w.reportStateLabel.text());self.assertIn('背景股票非建议对象',w.reportDetails.toPlainText())
    def test_new_public_announcement_marks_report_stale_without_rewriting(self):
        from tests.core.test_briefing import row
        w=self.open(3);w.copy_prompt();w.importText.setPlainText(ANSWER);w.import_report()
        w.store.record_fetch('001330.SZ','announcement',[row('001330.SZ')],now=time.time())
        w.refresh_report_status();self.assertIn('数据已变化',w.reportStateLabel.text())
        self.assertEqual(w.store.reports()[0]['content'],ANSWER)
