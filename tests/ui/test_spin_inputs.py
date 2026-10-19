"""3A数字控件的鼠标、键盘、直接输入及重启保存回归。"""
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QStyle,QStyleOptionSpinBox
from stockwidget.core.risk import RiskStore
from tests.ui import test_risk as fixture
import unittest


class SpinInputTests(unittest.TestCase):
    setUp=fixture.RiskUiTests.setUp
    tearDown=fixture.RiskUiTests.tearDown
    @classmethod
    def setUpClass(cls):
        cls.qt=QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)
    def test_vertical_mouse_targets_on_all_three_spinboxes(self):
        w=self.panel.risk.open(1);self.qt.processEvents()
        for name in ('failures','stale_seconds','cooldown'):
            with self.subTest(name=name):
                spin=getattr(w,name+'Spin');option=QStyleOptionSpinBox();spin.initStyleOption(option)
                up=spin.style().subControlRect(QStyle.CC_SpinBox,option,QStyle.SC_SpinBoxUp,spin)
                down=spin.style().subControlRect(QStyle.CC_SpinBox,option,QStyle.SC_SpinBoxDown,spin)
                self.assertEqual(up.center().x(),down.center().x())
                self.assertLess(up.center().y(),down.center().y())
                before=spin.value();QTest.mouseClick(spin,Qt.LeftButton,pos=up.center())
                self.assertEqual(spin.value(),before+spin.singleStep())
                QTest.mouseClick(spin,Qt.LeftButton,pos=down.center())
                self.assertEqual(spin.value(),before)
    def test_keyboard_and_direct_input_then_restart_restore(self):
        w=self.panel.risk.open(1);self.qt.processEvents()
        values={'failures':9,'stale_seconds':600,'cooldown':3600}
        for name,value in values.items():
            with self.subTest(name=name):
                spin=getattr(w,name+'Spin');spin.setFocus();before=spin.value()
                QTest.keyClick(spin,Qt.Key_Up);self.assertEqual(spin.value(),before+1)
                QTest.keyClick(spin,Qt.Key_Down);self.assertEqual(spin.value(),before)
                spin.lineEdit().selectAll();QTest.keyClicks(spin.lineEdit(),str(value));QTest.keyClick(spin,Qt.Key_Return)
                self.assertEqual(spin.value(),value)
        w.saveAlertsButton.click()
        reopened=RiskStore(self.panel.risk.store.path)
        saved=reopened.load_settings(1,'001330.SZ')
        self.assertEqual({k:saved[k] for k in values},values)
        w.cooldownSpin.setValue(1800)
        self.assertIn('30 分钟',w.cooldownSpin.text())
        w.bind_position(self.store.load())
        self.assertEqual(w.cooldownSpin.value(),3600)
        self.assertIn('60 分钟',w.cooldownSpin.text())
