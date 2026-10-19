"""独立候选清单、证据约束与报告身份；临时库，不使用真实个人数据。"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from stockwidget.core.portfolio import PositionStore,PortfolioError
from stockwidget.core.briefing import BriefingStore,build_packet,scope_caption,report_text
from stockwidget.core.candidates import CandidateStore,research_universe,Candidate
from stockwidget.core.advice import report_status
from stockwidget.data.research import ResearchError
from tests.core.test_stage4 import NOW,ANSWER


class CandidateTests(unittest.TestCase):
    def setUp(self):
        root=Path(self.enterContext(TemporaryDirectory()));self.hold=PositionStore(root/'positions.sqlite3')
        self.db=BriefingStore(root/'research.sqlite3');self.candidates=CandidateStore(self.db)
    def add(self):
        self.candidates.save('浦发银行','600000.SH');return self.candidates.load_all()[0]
    def packet(self,positions,**kw):return build_packet(positions,{},'sina',{},now=NOW,**kw)
    def test_restart_preserves_independent_candidates_and_original_holdings(self):
        before=self.hold.path.read_bytes();c=self.add()
        self.assertEqual(CandidateStore(BriefingStore(self.db.path)).load_all(),[c])
        self.assertFalse(hasattr(c,'total'));self.assertFalse(hasattr(c,'cost'))
        self.assertEqual(self.hold.path.read_bytes(),before);self.assertEqual(self.hold.load().locked,200)
    def test_validation_duplicate_held_security_and_invalid_a_share(self):
        self.add()
        for name,code in [('', '600001.SH'),('重复','600000.SH'),('港股','00700.HK'),('非股','000001.SH')]:
            with self.assertRaises(PortfolioError):self.candidates.save(name,code)
        with self.assertRaises(PortfolioError):self.candidates.save('博纳','001330.SZ',held_codes={'001330.SZ'})
    def test_archive_and_readd_keep_old_report_stale(self):
        c=self.add();packet=self.packet([c],universe='candidates');pid=self.db.save_packet(packet)
        self.db.import_report(pid,'候选研究',ANSWER,now=NOW);r=self.db.reports()[0]
        self.candidates.archive(c.code)
        self.assertIn('数据已变化',report_status(r,self.hold.load_all(),{},'sina',now=NOW))
        self.candidates.save('浦发更名',c.code);new=self.candidates.load_all()[0]
        self.assertEqual(c.position_id,new.position_id);self.assertGreater(new.version,c.version)
        self.assertIn('数据已变化',report_status(r,[new],{},'sina',now=NOW))
        self.assertEqual(self.db.reports()[0]['content'],ANSWER)
    def test_adding_candidate_never_invalidates_existing_all_holdings_scope(self):
        p=self.packet(self.hold.load_all(),scope_all=True);pid=self.db.save_packet(p);self.db.import_report(pid,'持仓',ANSWER,now=NOW)
        c=self.add();current=research_universe(self.hold.load_all(),[c])
        self.assertNotIn('数据已变化',report_status(self.db.reports()[0],current,{},'sina',now=NOW))
    def test_candidate_packet_has_no_fake_zero_position_or_pnl(self):
        c=self.add();packet=self.packet([c],include_private=True,universe='candidates')
        for text in ('未登记持仓','财务与估值：尚未提取','未读全文','待核验'):self.assertIn(text,packet['body'])
        for text in ('成本 0','本地手动持仓：','参考市值','参考浮动盈亏'):self.assertNotIn(text,packet['body'])
    def test_comparison_excludes_candidate_from_asset_totals_and_deduplicates_held_code(self):
        c=self.add();p=self.hold.load();items=research_universe([p],[c,Candidate('观察博纳',p.code)])
        self.assertEqual(len(items),2)
        q={p.code:dict(current_price=10,timestamp=NOW),c.code:dict(current_price=999,timestamp=NOW)}
        packet=build_packet(items,q,'sina',{},now=NOW,include_private=True,universe='combined',purpose='comparison')
        self.assertIn('总市值 12000.00',packet['body']);self.assertIn('候选股不计入资产',packet['body'])
        self.assertIn('不是用户全部资产仓位',packet['body']);self.assertIn('未提供已核实的行业',packet['body'])
    def test_quality_prompt_requires_counter_evidence_verification_and_invalidation(self):
        text=self.packet(self.hold.load_all())['prompt']
        for term in ('支持证据和反对证据','推翻判断','经营现金流','扣非利润','报告期','什么变化出现就放弃','资料不足不强排'):
            self.assertIn(term,text)
        self.assertIn('禁止建议卖出锁定仓',text);self.assertIn('可以给有依据、附条件的参考股数',text)
    def test_discovery_works_without_positions_and_never_claims_auto_screening(self):
        packet=self.packet([],purpose='discovery',focus='关注现金流')
        for text in ('不得只分析我当前持仓','最多3只','若无法联网','不凭记忆编造名单','关注现金流'):self.assertIn(text,packet['prompt'])
        pid=self.db.save_packet(packet);self.db.import_report(pid,'其他A股研究','普通研究回答',now=NOW)
        r=self.db.reports()[0];self.assertIn('全A股候选研究',scope_caption(r));self.assertIn('背景股票非建议对象',report_text(r))
    def test_corrupt_candidate_storage_is_reported_and_never_reset(self):
        with self.db._db() as db:db.execute("INSERT INTO preferences VALUES('research_candidates_v1',?)",(json.dumps({'wrong':True}),))
        with self.assertRaises(ResearchError):self.candidates.load_all()
        self.assertEqual(self.hold.load().locked,200)
    def test_public_evidence_updates_or_expiry_invalidate_old_report(self):
        from stockwidget.core.advice import evidence_context
        from tests.core.test_briefing import row
        p=self.hold.load();packet=self.packet([p]);pid=self.db.save_packet(packet);self.db.import_report(pid,'旧证据',ANSWER,now=NOW)
        record=self.db.reports()[0]
        cache={(p.code,'announcement'):([dict(row(p.code),fetched=NOW)],dict(last_success=NOW,error=''))}
        self.assertIn('数据已变化',report_status(record,[p],{},'sina',now=NOW,evidence=evidence_context([p],cache,NOW)))
        initial=evidence_context([p],cache,NOW)
        cache[(p.code,'announcement')][1]['last_success']=NOW+10
        self.assertEqual(initial,evidence_context([p],cache,NOW+10))
        self.assertNotEqual(initial,evidence_context([p],cache,NOW+21620))
    def test_candidate_limit_does_not_trigger_unbounded_public_fetches(self):
        for i in range(30):self.candidates.save('测试'+str(i),f'600{i:03d}.SH')
        with self.assertRaises(PortfolioError):self.candidates.save('超量','601999.SH')
