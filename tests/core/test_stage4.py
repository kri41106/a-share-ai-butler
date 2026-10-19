"""第四阶段：价格参考/方向判断分离、迁移备份、摘要与旧报告身份。"""
from contextlib import closing
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sqlite3
import json
import unittest
from stockwidget.core.window_rules import QUOTE_TIMEZONE
from stockwidget.core.quote_snapshot import quote_view,market_phase,last_completed_day
from stockwidget.core.portfolio import PositionStore
from stockwidget.core.briefing import BriefingStore,APP_ID,build_packet,report_text
from stockwidget.core.advice import extract_summary,report_status
from stockwidget.data.research import ResearchError
from stockwidget.data.closing_quotes import ClosingQuotes
from stockwidget.data.bars import Bar


def stamp(value):return datetime.fromisoformat(value).replace(tzinfo=QUOTE_TIMEZONE).timestamp()
NOW=stamp('2026-10-19 19:30:00')
CODE='001330.SZ'
ANSWER='【当前建议】观望\n【主要原因】数据不是实时；盈利仍待验证。\n【行动条件】核实财报和新行情后再评估。\n【主要风险】波动与经营不确定。\n【数据截至】2026-10-19 15:00，收盘非实时\n\n详细分析：\n原始正文完整保留。'

def legacy(path,wal=False):
    db=sqlite3.connect(path)
    if wal:db.execute('PRAGMA journal_mode=WAL')
    BriefingStore._schema(db);db.execute(f'PRAGMA application_id={APP_ID}');db.execute('PRAGMA user_version=1')
    db.execute('INSERT INTO packets VALUES(1,?,?,?,?,?)',(NOW,json.dumps([{'position_id':1,'code':CODE}]),'旧行情时间',1,'旧资料'))
    db.execute('INSERT INTO reports VALUES(7,1,?,?,?)',(NOW,'旧报告','原始文本不可丢失'))
    db.commit();return db


class Stage4Tests(unittest.TestCase):
    def setUp(self):
        self.path=Path(self.enterContext(TemporaryDirectory()))
        self.holdings=PositionStore(self.path/'positions.sqlite3');self.p=self.holdings.load()
        self.store=BriefingStore(self.path/'research.sqlite3')
    def quote(self,**values):
        q=dict(current_price=6.34,prev_close=6.68,date='2026-10-19',time='15:35:15',_code=CODE,_source='sina')
        q.update(values);return q
    def packet(self,quote=None,private=True,now=NOW,all_scope=False):
        return build_packet([self.p],{CODE:quote or self.quote()},'sina',{},now=now,include_private=private,scope_all=all_scope)
    def test_session_price_valid_but_stale_reference_not_live(self):
        now=stamp('2026-10-19 10:01:00')
        v=quote_view(self.quote(date='2026-10-19',time='10:00:00'),now=now,code=CODE)
        self.assertTrue(v['live']);self.assertTrue(v['available'])
        v=quote_view(self.quote(time='09:50:00'),now=now,code=CODE)
        self.assertFalse(v['live']);self.assertTrue(v['available']);self.assertIn('已过期',v['status'])
    def test_verified_close_survives_evening_weekend_and_holiday(self):
        q=self.quote(_price_kind='close',_close_date='2026-10-19')
        v=quote_view(q,now=NOW,code=CODE)
        self.assertEqual(v['kind'],'close');self.assertFalse(v['live']);self.assertIn('最近交易日收盘价',v['status'])
        holiday=stamp('2026-10-01 10:00:00')
        self.assertEqual(market_phase(holiday),'closed');self.assertEqual(last_completed_day(holiday),'2026-09-30')
        v=quote_view(self.quote(date='2026-09-30',time='15:00:00',_price_kind='close',_close_date='2026-09-30'),now=holiday)
        self.assertIn('最近交易日收盘价',v['status'])
        self.assertEqual(last_completed_day(stamp('2026-10-18 10:00:00')),'2026-10-16')
    def test_midday_is_reference_and_unknown_year_never_claimed_live(self):
        for date,time in [('2026-10-19','12:00:00'),('2027-01-15','10:00:00')]:
            v=quote_view(self.quote(date=date,time=time),now=stamp(date+' '+time))
            self.assertTrue(v['available']);self.assertFalse(v['live'])
    def test_missing_zero_future_and_mismatched_code_not_used_as_price(self):
        for q in [{},self.quote(current_price=0),self.quote(_code='600000.SH'),self.quote(timestamp=NOW+100)]:
            self.assertFalse(quote_view(q,now=NOW,code=CODE)['available'])
        v=quote_view(self.quote(date='',time=''),now=NOW,code=CODE)
        self.assertTrue(v['available']);self.assertIn('时间未知',v['status'])
    def test_failed_positive_snapshot_keeps_date_source_price_and_error(self):
        v=quote_view(self.quote(_last_error='网络超时'),now=NOW,code=CODE,failed=True)
        self.assertTrue(v['available']);self.assertFalse(v['live']);self.assertIn('网络超时',v['status'])
        packet=self.packet(self.quote(_last_error='网络超时'))
        for text in ('6.34','2026-10-19','新浪财经','网络超时','参考浮动盈亏 -3706.80'):self.assertIn(text,packet['body'])
    def test_packet_shows_close_reference_pnl_instead_of_losing_price(self):
        packet=self.packet(self.quote(_price_kind='close',_close_date='2026-10-19'))
        for text in ('收盘参考价格：6.34','收盘日期：2026-10-19','非实时','参考市值 7608.00','-3706.80','不超过200字','【行动条件】','选择观望'):self.assertIn(text,packet['prompt'])
        self.assertIn('不是用户全部资产仓位',packet['body'])
        self.assertIn('规则提示',packet['body'])
    def test_close_adapter_reuses_same_source_and_deduplicates_daily_request(self):
        enrich=ClosingQuotes();instruments={'sz001330':self.p.instrument()}
        bars=(Bar('2026-10-16',6,7,6,6.68,100),Bar('2026-10-19',6,7,6,6.34,200))
        with patch('stockwidget.data.closing_quotes.request_sina_bars',return_value=bars) as request,patch('stockwidget.data.closing_quotes.request_eastmoney_bars') as em:
            for _ in range(2):
                result=enrich.enrich(instruments,'sina',{'sz001330':self.quote()},NOW)
                self.assertEqual(result['sz001330']['_close_date'],'2026-10-19')
                self.assertEqual(result['sz001330']['time'],'15:35:15')
            request.assert_called_once();em.assert_not_called()
    def test_daily_in_progress_never_used_as_close_and_no_cost_fallback(self):
        enrich=ClosingQuotes();instruments={'sz001330':self.p.instrument()}
        bars=(Bar('2026-10-16',6,7,6,6.68),Bar('2026-10-19',6,7,6,99))
        with patch('stockwidget.data.closing_quotes.request_sina_bars',return_value=bars):
            result=enrich.enrich(instruments,'sina',{},stamp('2026-10-19 08:00:00'))
            self.assertEqual(result['sz001330']['current_price'],6.68)
            self.assertEqual(result['sz001330']['_close_date'],'2026-10-16')
    def test_fresh_session_never_requests_daily_and_failure_does_not_fabricate_close(self):
        enrich=ClosingQuotes();instruments={'sz001330':self.p.instrument()}
        with patch('stockwidget.data.closing_quotes.request_sina_bars') as req:
            q={'sz001330':self.quote(time='10:00:00')}
            self.assertEqual(enrich.enrich(instruments,'sina',q,stamp('2026-10-19 10:00:00')),q);req.assert_not_called()
    def test_quote_cache_restart_is_per_source_code_and_not_holdings(self):
        before=self.holdings.path.read_bytes()
        q=self.quote(_received_at=NOW,_price_kind='close',_close_date='2026-10-19')
        self.store.cache_quote(CODE,'sina',q)
        reopened=BriefingStore(self.store.path)
        self.assertEqual(reopened.load_quotes('sina')[CODE]['current_price'],6.34)
        self.assertEqual(reopened.load_quotes('eastmoney'),{})
        self.assertEqual(self.holdings.path.read_bytes(),before)
    def test_preference_remembers_explicit_choice_and_can_be_disabled(self):
        self.assertFalse(self.store.private_preference());self.store.private_preference(True)
        self.assertTrue(BriefingStore(self.store.path).private_preference())
        self.store.private_preference(False);self.assertFalse(BriefingStore(self.store.path).private_preference())
    def test_v1_migration_backups_history_and_repeated_runs_do_not_duplicate(self):
        path=self.path/'old.sqlite3'
        with closing(legacy(path)):pass
        updated=BriefingStore(path)
        self.assertTrue(updated.backup_path.exists());self.assertEqual(updated.reports()[0]['content'],'原始文本不可丢失')
        self.assertIn('旧报告',report_status(updated.reports()[0],[self.p],{},'sina',now=NOW))
        with closing(sqlite3.connect(updated.backup_path)) as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
        self.assertIsNone(BriefingStore(path).backup_path)
        with closing(sqlite3.connect(path)) as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
    def test_migration_rolls_back_ddl_and_preserves_old_data_on_failure(self):
        path=self.path/'bad.sqlite3'
        with closing(legacy(path)):pass
        original=BriefingStore._stage4_schema
        def fail(db):original(db);raise sqlite3.OperationalError('test')
        with patch.object(BriefingStore,'_stage4_schema',side_effect=fail),self.assertRaises(ResearchError):BriefingStore(path)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT content FROM reports').fetchone()[0],'原始文本不可丢失')
            self.assertNotIn('quote_cache',{x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
    def test_backup_failure_cancels_migration_and_wal_snapshot_is_preserved(self):
        path=self.path/'wal.sqlite3'
        with closing(legacy(path,wal=True)) as old:
            with patch.object(BriefingStore,'_backup_v1',side_effect=ResearchError('模拟备份失败')),self.assertRaises(ResearchError):BriefingStore(path)
            self.assertEqual(old.execute('PRAGMA user_version').fetchone()[0],1)
            updated=BriefingStore(path)
            with closing(sqlite3.connect(updated.backup_path)) as db:self.assertEqual(db.execute('SELECT id FROM reports').fetchone()[0],7)
    def test_summary_parse_keeps_raw_and_unrecognized_content_never_gets_fabricated_choice(self):
        fields=extract_summary(ANSWER);self.assertEqual(fields['当前建议'],'观望')
        for content in ['普通文本：先核实信息。',ANSWER.replace('【主要风险】','风险：'),ANSWER.replace('【当前建议】观望','【当前建议】自动交易')]:self.assertIsNone(extract_summary(content))
        packet_id=self.store.save_packet(self.packet());self.store.import_report(packet_id,'A',ANSWER,now=NOW+10)
        self.assertEqual(self.store.reports()[0]['content'],ANSWER)
    def test_generation_import_packet_and_quote_times_remain_distinct(self):
        packet_id=self.store.save_packet(self.packet())
        self.store.import_report(packet_id,'A',ANSWER,now=NOW+600,generated_at='2026-10-19 19:35')
        row=self.store.reports()[0];text=report_text(row)
        self.assertEqual(row['created'],NOW+600);self.assertEqual(row['packet_created'],NOW)
        self.assertEqual(row['generated_at'],'2026-10-19 19:35');self.assertIn('15:35:15',row['data_time'])
        self.assertIn('报告生成时间',text);self.assertIn('导入时间',text)
    def test_report_flags_quotes_positions_scope_changes_without_rewriting(self):
        q=self.quote(_price_kind='close',_close_date='2026-10-19');packet=self.packet(q,all_scope=True)
        pid=self.store.save_packet(packet);self.store.import_report(pid,'A',ANSWER,now=NOW+1)
        row=self.store.reports()[0];before=row['content']
        self.assertNotIn('数据已变化',report_status(row,[self.p],{CODE:q},'sina',now=NOW))
        self.assertIn('数据已变化',report_status(row,[self.p],{CODE:dict(q,current_price=7)},'sina',now=NOW))
        updated=self.holdings.update_position(name=self.p.name,code=CODE,total=1300,cost='9',expected_version=self.p.version)
        self.assertIn('数据已变化',report_status(row,[updated],{CODE:q},'sina',now=NOW))
        self.assertEqual(self.store.reports()[0]['content'],before)
        self.assertEqual(self.holdings.load().locked,200)
    def test_private_exclusion_and_data_insufficient_prompt_stays_cautious(self):
        packet=self.packet(private=False)
        for private in ('9.429','1200 股','锁定仓 200 股','策略仓 1000 股'):self.assertNotIn(private,packet['prompt'])
        self.assertIn('资料不足以支持方向判断时必须选择观望',packet['prompt'])
        self.assertIn('锁定仓不可卖',packet['prompt'])
    def test_corrupt_or_cross_source_cache_is_reported_without_reset(self):
        for q in ({'_code':CODE,'_source':'sina','current_price':'invalid'},self.quote(_source='eastmoney')):
            with closing(sqlite3.connect(self.store.path)) as db:
                db.execute('INSERT OR REPLACE INTO quote_cache VALUES(?,?,?)',(CODE,'sina',json.dumps(q)));db.commit()
            with self.assertRaises(ResearchError):self.store.load_quotes('sina')
            self.assertEqual(self.holdings.load(),self.p)
