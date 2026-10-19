"""行动摘要、只读数量校验及预算隐私；全部使用临时或模拟数据。"""
from dataclasses import replace
import json
import unittest
from stockwidget.core.advice import extract_summary,summary_text,checked_action
from stockwidget.core.briefing import build_packet
from tests.core import test_stage4 as fixture
from tests.core.test_stage4 import NOW,CODE,stamp
from tests.core.test_analysis_prompt import REPLY

class ActionAdviceTests(unittest.TestCase):
    setUp=fixture.Stage4Tests.setUp
    quote=fixture.Stage4Tests.quote
    def check(self,text,*,private=True,live=True,unchanged=True,budget=None,positions=None):
        now=stamp('2026-10-19 10:01:00')
        q=self.quote(time='10:00:30' if live else '09:00:00')
        row=dict(private=private,scope_json=json.dumps([{'code':CODE}]),context_json=json.dumps({'buy_budget':budget} if budget is not None else {}))
        before=self.holdings.path.read_bytes()
        result=checked_action({'操作思路':text},row,positions or [self.p],{CODE:q},'sina',now,unchanged=unchanged)['操作思路']
        self.assertEqual(self.holdings.path.read_bytes(),before)
        return result
    def test_new_six_field_summary_preserves_old_five_and_puts_actions_near_top(self):
        reply=REPLY.replace('一、研究身份','【操作思路】全部保留，核实三季报后再评估。\n一、研究身份')
        f=extract_summary(reply)
        self.assertEqual(f['操作思路'],'全部保留，核实三季报后再评估。')
        self.assertNotIn('操作思路',f['数据截至'])
        self.assertLess(summary_text(f).index('操作思路'),summary_text(f).index('主要原因'))
        self.assertNotIn('操作思路',extract_summary(REPLY))
        self.assertIsNone(extract_summary('普通文本：保持关注，原文不丢失。'))
    def test_valid_sell_only_checks_saved_strategy_without_writing(self):
        self.assertIn('仅核对本地策略仓',self.check('卖出 001330.SZ 500股；核实财报后再决定，锁定部分保留。'))
    def test_repeated_sells_aggregate_and_cannot_touch_locked_shares(self):
        for text in ('卖出 001330.SZ 1001股','卖出 001330.SZ 600股；卖出 001330.SZ 500股'):
            self.assertIn('未通过校验',self.check(text))
    def test_zero_strategy_and_permanent_lock_conflict_block(self):
        for p in (replace(self.p,total=200),replace(self.p,locked=0)):
            self.assertIn('未通过校验',self.check('卖出 001330.SZ 1股',positions=[p]))
    def test_no_live_data_no_privacy_or_changed_report_block_quantities(self):
        for kw in ({'live':False},{'private':False},{'unchanged':False}):
            self.assertIn('未通过校验',self.check('卖出 001330.SZ 500股',**kw))
    def test_other_stock_cannot_borrow_strategy_or_quote(self):
        self.assertIn('不在原资料范围',self.check('卖出 600000.SH 500股'))
    def test_unknown_stock_format_and_oversized_integer_fail_closed(self):
        for text in ('卖出博纳500股','卖出 001330.SZ -500股','卖出 001330.SZ '+('9'*5000)+'股'):
            self.assertIn('未通过校验',self.check(text))
    def test_buy_budget_is_independent_of_projected_sell_proceeds(self):
        self.assertIn('未提供预算',self.check('买入 001330.SZ 100股'))
        self.assertIn('超过',self.check('买入 001330.SZ 200股',budget='1000'))
        self.assertIn('超过',self.check('卖出 001330.SZ 1000股；买入 001330.SZ 200股',budget='1000'))
        text=self.check('买入 001330.SZ 100股',budget='1000')
        self.assertIn('买入 001330.SZ 100股',text);self.assertIn('未验证实际成交或申报合规',text)
    def test_repeated_buys_share_one_budget(self):
        self.assertIn('超过',self.check('买入 001330.SZ 100股；买入 001330.SZ 100股',budget='1000'))
    def test_no_quantity_hold_condition_does_not_require_fresh_price(self):
        self.assertEqual('全部保留，暂不新增；核实现金流后重评。',self.check('全部保留，暂不新增；核实现金流后重评。',live=False,private=False))
    def test_budget_is_opt_in_in_json_and_does_not_reset_positions(self):
        for private in (True,False):
            result=build_packet([self.p],{},'sina',{},now=NOW,include_private=private,buy_budget='876.54')
            self.assertEqual('876.54' in result['prompt'],private)
            self.assertEqual('buy_budget' in result['context'],private)
        self.assertEqual(self.holdings.load(),self.p)
