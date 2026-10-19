"""实际复制、旧预览和三个入口不会覆盖统一研究要求。"""
import json
import unittest
from PySide6.QtWidgets import QApplication
from stockwidget.core.analysis_prompt import research_instructions,TEMPLATE_ID
from tests.ui import test_candidates as fixture
from tests.core.test_analysis_prompt import REPLY


class AnalysisPromptUiTests(unittest.TestCase):
    setUp=fixture.CandidateUiTests.setUp
    tearDown=fixture.CandidateUiTests.tearDown
    setUpClass=classmethod(fixture.CandidateUiTests.setUpClass.__func__)
    open=fixture.CandidateUiTests.open
    add=fixture.CandidateUiTests.add
    def test_copy_from_all_modes_rebuilds_new_template_and_preserves_valid_json(self):
        w=self.add()
        for private in (False,True):
            w.privateCheck.setChecked(private)
            for mode in (0,1,2):
                w.taskCombo.setCurrentIndex(mode);w.copy_prompt();text=QApplication.clipboard().text()
                self.assertTrue(text.startswith(research_instructions(private)))
                self.assertEqual(json.loads(text.split('引用资料JSON：\n',1)[1])['资料行'],w.draft['body'].splitlines())
                self.assertIn('最多3只',text);self.assertNotIn('最多3至5只',text)
    def test_old_preview_or_saved_packet_cannot_replace_current_clipboard_template(self):
        w=self.open(2);w.copy_prompt();packet_id=w.packetCombo.currentData();before=self.store.path.read_bytes()
        w.promptText.setPlainText('旧提示词：只分析当前股票，最多3至5只')
        w.draft=dict(w.draft,prompt='旧提示词');w.copy_prompt()
        self.assertIn(TEMPLATE_ID,QApplication.clipboard().text());self.assertNotIn('旧提示词',QApplication.clipboard().text())
        w.packetCombo.setCurrentIndex(w.packetCombo.findData(packet_id));w.copy_prompt()
        self.assertTrue(QApplication.clipboard().text().startswith(research_instructions(False)))
        self.assertEqual(self.store.path.read_bytes(),before)
    def test_privacy_preference_switch_applies_to_new_lock_policy_immediately(self):
        w=self.open(2);w.privateCheck.setChecked(True);w.copy_prompt()
        self.assertIn('博纳影业200股永久锁定',QApplication.clipboard().text())
        w.privateCheck.setChecked(False);w.copy_prompt()
        self.assertNotIn('博纳影业200股永久锁定',QApplication.clipboard().text());self.assertNotIn('9.429',QApplication.clipboard().text())
        self.assertIn('未包含持仓',w.privacyStateLabel.text())
    def test_new_format_import_shows_target_and_keeps_full_six_section_original(self):
        w=self.open(3);w.copy_prompt();w.importText.setPlainText(REPLY);w.import_report()
        self.assertIn('观望（针对现有持仓）',w.suggestionText.toPlainText());self.assertNotIn('一、研究身份',w.suggestionText.toPlainText())
        self.assertFalse(w.reportDetails.isVisible());w.expandReportCheck.setChecked(True)
        self.assertIn(REPLY,w.reportDetails.toPlainText());self.assertEqual(w.store.reports()[0]['content'],REPLY)
