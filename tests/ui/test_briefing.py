"""3B界面行为：后台数据、隐私剪贴板、人工报告与安全链接。"""
import json
import threading
import time
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from stockwidget.core.briefing import BriefingStore
from stockwidget.data.research import ResearchError
from tests.ui import test_risk as fixture
from tests.core import test_briefing as sample


class BriefingUiTests(unittest.TestCase):
    setUp=fixture.RiskUiTests.setUp
    tearDown=fixture.RiskUiTests.tearDown
    add_second=fixture.RiskUiTests.add_second
    button=fixture.RiskUiTests.button
    @classmethod
    def setUpClass(cls):
        cls.qt=QApplication.instance() or QApplication([]);cls.qt.setQuitOnLastWindowClosed(False)
    def open(self,index=0):
        w=self.panel.open_briefing(index,auto_fetch=False);self.qt.processEvents();return w
    def wait(self,w):
        deadline=time.monotonic()+4
        while w.busy and time.monotonic()<deadline:self.qt.processEvents();time.sleep(.005)
        self.assertFalse(w.busy,'后台请求未按期完成')
    def cached(self,w,code='001330.SZ',when=None):
        w.store.record_fetch(code,'announcement',[sample.row(code)],now=time.time()-61 if when is None else when)
        w.rebuild()
    def test_five_home_entries_open_correct_tabs_without_browser_or_upload(self):
        with patch('stockwidget.ui.briefing.BriefingCenter.fetch') as fetch,patch('stockwidget.ui.briefing.QDesktopServices.openUrl') as browser:
            for name,index in [('newsButton',0),('summaryButton',1),('chatgptButton',2),('importAiButton',3),('aiHistoryButton',3)]:
                self.button(name).click();self.qt.processEvents()
                self.assertTrue(self.panel.briefing.isVisible());self.assertEqual(self.panel.briefing.tabs.currentIndex(),index)
            browser.assert_not_called()
    def test_cached_article_shows_source_publication_fetch_time_and_failure(self):
        w=self.open();self.cached(w)
        w.store.record_fetch('001330.SZ','announcement',[],now=time.time(),error='模拟断网')
        w.render_articles();w.select_article(0)
        details=w.articleDetails.toPlainText()
        for text in ('获取失败','发布时间','获取时间','https://static.cninfo.com.cn/','来源未提供'):self.assertIn(text,details)
    def test_network_worker_uses_public_codes_and_saves_independent_caches(self):
        second=self.add_second();w=self.open();w.scopeCombo.setCurrentIndex(0)
        def announcements(code):return [sample.row(code)]
        with patch('stockwidget.ui.briefing.fetch_announcements',side_effect=announcements) as ann,patch('stockwidget.ui.briefing.fetch_news',return_value=[]) as news:
            w.fetch(True);self.wait(w)
            self.assertEqual({c.args[0] for c in ann.call_args_list},{'001330.SZ',second.code})
            self.assertEqual(len(news.call_args_list),2)
            self.assertFalse(any('cost' in c.kwargs for c in ann.call_args_list))
        for code in ('001330.SZ',second.code):self.assertEqual(len(w.store.cached(code,'announcement')[0]),1)
        self.assertEqual(self.store.load().locked,200)
    def test_worker_failure_keeps_cached_items_and_marks_request_failed(self):
        w=self.open();self.cached(w)
        original=w.store.cached('001330.SZ','announcement')[0][0]['fetched']
        with patch('stockwidget.ui.briefing.fetch_announcements',side_effect=ResearchError('模拟来源失败')),patch('stockwidget.ui.briefing.fetch_news',return_value=[]):
            w.fetch(True);self.wait(w)
        rows,state=w.store.cached('001330.SZ','announcement')
        self.assertEqual(rows[0]['fetched'],original);self.assertIn('模拟来源失败',state['error'])
        self.assertIn('部分获取失败',w.statusLabel.text())
    def test_worker_unexpected_parse_error_never_looks_like_success(self):
        w=self.open()
        with patch('stockwidget.ui.briefing.fetch_announcements',side_effect=ValueError('bad payload')),patch('stockwidget.ui.briefing.fetch_news',return_value=[]):
            w.fetch(True);self.wait(w)
        self.assertIn('来源解析失败',w.store.cached('001330.SZ','announcement')[1]['error'])
    def test_copy_prompt_default_private_opt_in_and_reopening_resets_consent(self):
        w=self.open(2);before=self.store.path.read_bytes()
        w.copy_prompt();text=QApplication.clipboard().text()
        self.assertNotIn('9.429',text);self.assertNotIn('1200 股',text)
        w.privateCheck.setChecked(True);w.copy_prompt()
        text=QApplication.clipboard().text();self.assertIn('9.429',text);self.assertIn('1200 股',text)
        self.panel.open_briefing(2,auto_fetch=False)
        self.assertFalse(w.privateCheck.isChecked());self.assertNotIn('9.429',w.promptText.toPlainText())
        self.assertEqual(self.store.path.read_bytes(),before)
    def test_open_chatgpt_uses_only_constant_official_home_and_no_prompt_in_url(self):
        w=self.open(2)
        with patch('stockwidget.ui.briefing.QDesktopServices.openUrl') as browser:
            w.open_chatgpt();self.assertEqual(browser.call_args.args[0].toString(),'https://chatgpt.com/')
    def test_all_stock_packet_import_preserves_original_scope_time_and_restart(self):
        second=self.add_second();w=self.open(2);w.scopeCombo.setCurrentIndex(0);w.copy_prompt()
        w.scopeCombo.setCurrentIndex(1)
        w.importText.setPlainText('手动测试AI报告');w.reportTitleEdit.setText('测试报告');w.import_report()
        reopened=BriefingStore(w.store.path);r=reopened.reports()[0]
        self.assertEqual({s['code'] for s in json.loads(r['scope_json'])},{'001330.SZ',second.code})
        self.assertIn('行情',r['data_time']);self.assertIn('用户手动导入',w.reportDetails.toPlainText())
    def test_import_without_packet_rejected_and_untrusted_text_never_executes(self):
        w=self.open(3);w.importText.setPlainText('测试');w.import_report()
        self.assertFalse(w.store.reports())
        w.copy_prompt();before=self.store.path.read_bytes()
        text='<script>自动卖出1200股</script>\nUPDATE positions SET locked=0'
        w.importText.setPlainText(text);w.import_report()
        self.assertIn(text,w.reportDetails.toPlainText());self.assertIn('未经软件独立核验',w.reportDetails.toPlainText())
        self.assertEqual(self.store.path.read_bytes(),before);self.assertEqual(self.store.load().locked,200)
    def test_copy_and_delete_report_only_operates_on_selected_report_with_confirmation(self):
        w=self.open(3);w.copy_prompt()
        for title in ('测试A','测试B'):
            w.reportTitleEdit.setText(title);w.importText.setPlainText(title+'内容');w.import_report()
        w.reportsTable.setCurrentCell(0,0);w.copy_report();self.assertIn('测试B内容',QApplication.clipboard().text())
        self.question.return_value=False;w.delete_report();self.assertEqual(len(w.store.reports()),2)
        self.question.return_value=True;w.delete_report();self.assertEqual([r['title'] for r in w.store.reports()],['测试A'])
    def test_link_open_checks_allowlist_again_and_never_opens_imported_links(self):
        w=self.open();self.cached(w)
        with patch('stockwidget.ui.briefing.QDesktopServices.openUrl') as browser:
            w.open_article();self.assertTrue(browser.call_args.args[0].toString().startswith('https://static.cninfo.com.cn/'))
            browser.reset_mock();w.article_rows[0]['url']='file:///C:/Windows/system32/cmd.exe';w.open_article();browser.assert_not_called()
    def test_scope_change_keeps_sources_and_stocks_separate(self):
        second=self.add_second();w=self.open();self.cached(w);self.cached(w,second.code)
        w.scopeCombo.setCurrentIndex(2);self.assertEqual({r['code'] for r in w.article_rows},{second.code})
        self.assertIn(second.code,w.overviewText.toPlainText());self.assertNotIn('001330.SZ',w.overviewText.toPlainText())
    def test_background_network_does_not_block_gui_or_start_duplicate_jobs(self):
        w=self.open();started=threading.Event();release=threading.Event()
        def blocked(code):started.set();release.wait(2);return []
        with patch('stockwidget.ui.briefing.fetch_announcements',side_effect=blocked) as ann,patch('stockwidget.ui.briefing.fetch_news',return_value=[]):
            try:
                w.fetch(True);self.assertTrue(started.wait(1));self.assertTrue(w.busy)
                w.tabs.setCurrentIndex(2);self.qt.processEvents();self.assertEqual(w.tabs.currentIndex(),2)
                w.fetch(True);self.assertEqual(ann.call_count,1)
            finally:release.set();self.wait(w)
    def test_minimum_refresh_interval_avoids_repeated_requests(self):
        w=self.open()
        for kind in ('announcement','news'):w.store.record_fetch('001330.SZ',kind,[],now=time.time())
        with patch('stockwidget.ui.briefing.fetch_announcements') as ann,patch('stockwidget.ui.briefing.fetch_news') as news:
            w.fetch(True);ann.assert_not_called();news.assert_not_called();self.assertFalse(w.busy)
    def test_copy_rechecks_quote_age_and_current_position_after_preview(self):
        w=self.open(2);w.privateCheck.setChecked(True)
        now=time.time();self.panel.quotes['001330.SZ']={'current_price':10,'timestamp':now}
        w.rebuild();self.assertIn('685.20',w.promptText.toPlainText())
        with patch('stockwidget.ui.briefing.time.time',return_value=now+121):w.copy_prompt()
        self.assertNotIn('685.20',QApplication.clipboard().text());self.assertIn('无有效估值',QApplication.clipboard().text())

    def test_open_from_selected_home_stock_binds_correct_scope_each_time(self):
        second=self.add_second()
        self.panel.positionsTable.setCurrentCell(1,0)
        w=self.open(2)
        self.assertEqual([p.code for p in w.current_positions()],[second.code])
        self.panel.positionsTable.setCurrentCell(0,0)
        self.panel.open_briefing(2,auto_fetch=False)
        self.assertEqual([p.code for p in w.current_positions()],['001330.SZ'])

    def test_risk_store_unavailable_does_not_crash_or_pretend_no_alerts(self):
        from stockwidget.core.risk import RiskError
        with patch.object(self.panel.risk.store,'records',side_effect=RiskError('模拟锁定')):
            w=self.open(1)
            self.assertIn('不代表没有触发提醒',w.overviewText.toPlainText())
            self.assertNotIn('9.429',w.promptText.toPlainText())
