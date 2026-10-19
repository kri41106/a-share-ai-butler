"""新版行动卡片的隐私、历史保留与数据变化回退。"""
import unittest
from PySide6.QtWidgets import QApplication
from tests.ui import test_candidates as fixture
from tests.core.test_analysis_prompt import REPLY

class ActionAdviceUiTests(unittest.TestCase):
    setUp=fixture.CandidateUiTests.setUp
    tearDown=fixture.CandidateUiTests.tearDown
    setUpClass=classmethod(fixture.CandidateUiTests.setUpClass.__func__)
    open=fixture.CandidateUiTests.open
    def test_budget_only_in_explicit_private_copy_and_old_preview_cannot_restore_it(self):
        w=self.open(2);w.buyBudgetEdit.setText('876.54');w.copy_prompt()
        self.assertNotIn('876.54',QApplication.clipboard().text())
        w.privateCheck.setChecked(True);w.copy_prompt()
        self.assertIn('本人填写本次买入预算：876.54',QApplication.clipboard().text())
        w.privateCheck.setChecked(False);w.copy_prompt()
        self.assertNotIn('876.54',QApplication.clipboard().text())
    def test_invalid_budget_prevents_copy_of_stale_draft(self):
        w=self.open(2);w.privateCheck.setChecked(True);w.copy_prompt()
        QApplication.clipboard().setText('原剪贴板');w.buyBudgetEdit.setText('-1');w.copy_prompt()
        self.assertEqual(QApplication.clipboard().text(),'原剪贴板')
    def test_action_near_top_with_plaintext_original_kept_and_stale_quantities_hidden(self):
        w=self.open(3);w.privateCheck.setChecked(True);w.copy_prompt()
        answer=REPLY.replace('一、研究身份','【操作思路】卖出 001330.SZ 500股；核价后再决定。\n一、研究身份')
        before=self.store.path.read_bytes();w.importText.setPlainText(answer);w.import_report()
        self.assertIn('数量方案未通过校验',w.suggestionText.toPlainText())
        self.assertLess(w.suggestionText.toPlainText().index('操作思路'),w.suggestionText.toPlainText().index('主要原因'))
        self.assertIn(answer,w.reportDetails.toPlainText())
        self.assertEqual(w.store.reports()[0]['content'],answer)
        self.assertEqual(self.store.path.read_bytes(),before)
