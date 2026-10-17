"""用户可观察的规则、持久去重、故障回滚与只读持仓隔离。"""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from dataclasses import replace
import sqlite3
import unittest

from stockwidget.core.portfolio import PositionStore
from stockwidget.core.risk import RiskStore, RiskError, analyze, settings
from tests.core.test_multi_portfolio import make_v1

NOW = 1800000000


class RiskTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.path = Path(folder)
        self.holdings = PositionStore(self.path/'positions.sqlite3')
        self.position = self.holdings.load()
        self.store = RiskStore(self.path/'risk.sqlite3')

    def enable(self, **options):
        self.store.save_settings(self.position.position_id,self.position.code,options)

    def quote(self, price=10, **options):
        return dict(current_price=price,prev_close=10,timestamp=NOW,**options)

    def process(self, quote=None, *, position=None, now=NOW, failures=0):
        return self.store.process(position or self.position,quote,'sina',now=now,failures=failures)

    def records(self):
        return self.store.records()

    def test_defaults_no_price_or_percentage_presets_and_no_notifications(self):
        options = self.store.load_settings(1,self.position.code)
        self.assertTrue(all(not options[k+'_enabled'] for k in ('above','below','rise','fall','data')))
        self.assertTrue(all(options[k] is None for k in ('above','below','rise','fall')))
        self.assertEqual(self.process(self.quote(1))[1],[])

    def test_invalid_settings_rejected_without_changing_saved_options(self):
        self.enable(above_enabled=True,above='9')
        before = self.store.load_settings(1,self.position.code)
        for values in ({'above_enabled':True},{'above':-1},{'rise':'nan'}, {'fall':1001},
                       {'cooldown':0},{'failures':0},{'failures':1.5},{'stale_seconds':60}):
            with self.subTest(values=values), self.assertRaises(RiskError):
                self.enable(**values)
            self.assertEqual(self.store.load_settings(1,self.position.code),before)

    def test_high_low_strict_comparison(self):
        for kind, price, threshold in [('above',11,10),('below',9,10)]:
            with self.subTest(kind=kind):
                self.enable(**{kind+'_enabled':True,kind:threshold})
                self.assertFalse(self.process(self.quote(10))[1])
                notices = self.process(self.quote(price))[1]
                self.assertEqual([n['kind'] for n in notices],[kind])
                self.assertIn('新浪财经',notices[0]['source'])
                self.assertIn('北京时间',notices[0]['quote_time'])

    def test_percentage_threshold_is_inclusive_and_correct_sign(self):
        for kind, price in [('rise','10.5'),('fall','9.5')]:
            with self.subTest(kind=kind):
                self.enable(**{kind+'_enabled':True,kind:'5'})
                self.assertFalse(self.process(self.quote(10))[1])
                notices = self.process(self.quote(price))[1]
                self.assertEqual([n['kind'] for n in notices],[kind])
                self.assertIn('5%',notices[0]['basis'])

    def test_stale_quote_never_triggers_price_conditions(self):
        self.enable(above_enabled=True,above='9',rise_enabled=True,rise='1')
        result, notices = self.process(self.quote(12),now=NOW+121)
        self.assertEqual(notices,[])
        self.assertIn('行情已过期',result.title)
        self.assertIsNone(result.conditions['above'])
        self.assertNotIn('当前价格低于',result.text)

    def test_failed_request_blocks_price_judgment_even_if_last_quote_fresh(self):
        self.enable(above_enabled=True,above=9)
        result, notices = self.process(self.quote(),failures=1)
        self.assertIn('更新失败',result.title)
        self.assertEqual(notices,[])

    def test_invalid_future_and_unknown_times_do_not_judge_price(self):
        self.enable(below_enabled=True,below=20)
        for quote in ({'current_price':10},dict(self.quote(),timestamp=NOW+61),dict(self.quote(),timestamp='bad')):
            result, notices = self.process(quote)
            self.assertIsNone(result.conditions['below'])
            self.assertFalse(notices)

    def test_missing_or_invalid_quote_no_crash_or_made_up_indicators(self):
        for quote in (None,{},self.quote('nan'),self.quote(0),self.quote(-1)):
            result, notices = self.process(quote)
            self.assertIn('无法判断',result.title)
            self.assertFalse(notices)
            self.assertIn('非AI模型',result.text)
            self.assertNotIn('主力',result.text)

    def test_missing_prev_close_does_not_trigger_percentage_but_price_still_works(self):
        self.enable(rise_enabled=True,rise=1,above_enabled=True,above=9)
        result, notices = self.process(dict(self.quote(),prev_close=None))
        self.assertIsNone(result.conditions['rise'])
        self.assertIn('昨收价缺失',result.text)
        self.assertEqual([n['kind'] for n in notices],['above'])

    def test_analysis_cost_and_lock_snapshots_do_not_mutate_holdings(self):
        before = self.holdings.load()
        result,_ = self.process(self.quote(8))
        self.assertIn('低于持仓成本',result.title)
        self.assertIn('锁定仓 200 股',result.text)
        self.assertEqual(self.holdings.load(),before)

    def test_two_stocks_independent_configuration_and_analysis(self):
        second = self.holdings.add_position(name='第二只',code='600000.SH',total=600,cost=15)
        self.enable(above_enabled=True,above=9)
        first,notice = self.process(self.quote(10))
        other,none = self.process(self.quote(10),position=second)
        self.assertTrue(notice)
        self.assertFalse(none)
        self.assertNotEqual(first.title,other.title)
        self.assertTrue(all(row['code']==self.position.code for row in self.records()))

    def test_same_condition_never_repeats_while_true_even_after_cooldown(self):
        self.enable(above_enabled=True,above=9,cooldown=60)
        self.assertEqual(len(self.process(self.quote())[1]),1)
        for offset in (30,60,6000):
            self.assertFalse(self.process(dict(self.quote(),timestamp=NOW+offset),now=NOW+offset)[1])
        self.assertEqual(len(self.records()),1)

    def test_recovery_rearms_and_cooldown_waits_without_spam(self):
        self.enable(above_enabled=True,above=9,cooldown=60)
        self.process(self.quote(10))
        self.process(self.quote(8),now=NOW+10)
        self.assertFalse(self.process(self.quote(10),now=NOW+20)[1])
        self.assertEqual(len(self.process(self.quote(10),now=NOW+61)[1]),1)
        self.assertFalse(self.process(self.quote(10),now=NOW+62)[1])

    def test_missing_data_does_not_rearm_price_latch(self):
        self.enable(above_enabled=True,above=9,cooldown=60)
        self.process(self.quote())
        self.process(None,now=NOW+30,failures=1)
        self.assertFalse(self.process(self.quote(),now=NOW+61)[1])
        self.assertEqual(len(self.records()),1)

    def test_restart_preserves_settings_records_analysis_and_dedup(self):
        self.enable(above_enabled=True,above=9,cooldown=60)
        self.process(self.quote())
        self.store = RiskStore(self.store.path)
        self.assertTrue(self.store.load_settings(1,self.position.code)['above_enabled'])
        self.assertEqual(len(self.records()),1)
        self.assertTrue(self.store.records(1,self.position.code,analyses=True))
        self.assertFalse(self.process(dict(self.quote(),timestamp=NOW+600),now=NOW+600)[1])

    def test_data_failure_threshold_and_recovery(self):
        self.enable(data_enabled=True,failures=3,cooldown=60)
        for count in (1,2):
            self.assertFalse(self.process(None,failures=count)[1])
        self.assertEqual([n['kind'] for n in self.process(None,failures=3)[1]],['data'])
        self.assertFalse(self.process(None,failures=4)[1])
        self.process(dict(self.quote(),timestamp=NOW+100),now=NOW+100)
        self.assertEqual(len(self.process(None,now=NOW+200,failures=3)[1]),1)

    def test_nontrading_stale_condition_remains_latched_across_days(self):
        self.enable(data_enabled=True,cooldown=60)
        self.assertEqual(len(self.process(self.quote(),now=NOW+121)[1]),1)
        for days in (1,2,3):
            self.assertFalse(self.process(self.quote(),now=NOW+days*86400)[1])
        self.assertEqual(len(self.records()),1)

    def test_custom_stale_threshold_does_not_relax_120_second_price_gate(self):
        self.enable(data_enabled=True,stale_seconds=600,above_enabled=True,above=9)
        self.assertFalse(self.process(self.quote(),now=NOW+200)[1])
        self.assertEqual([n['kind'] for n in self.process(self.quote(),now=NOW+601)[1]],['data'])

    def test_settings_change_honors_cooldown_and_idempotent_save_not_rearm(self):
        self.enable(above_enabled=True,above=9,cooldown=60)
        self.process(self.quote())
        self.enable(above_enabled=True,above=9,cooldown=60)
        self.assertFalse(self.process(self.quote(),now=NOW+61)[1])
        self.enable(above_enabled=True,above=8,cooldown=60)
        self.assertFalse(self.process(self.quote(),now=NOW+20)[1])
        self.assertEqual(len(self.process(self.quote(),now=NOW+61)[1]),1)

    def test_disable_condition_retains_history_and_stops_notifications(self):
        self.enable(above_enabled=True,above=9)
        self.process(self.quote())
        self.enable()
        self.assertFalse(self.process(self.quote(),now=NOW+61)[1])
        self.assertEqual(len(self.records()),1)

    def test_mark_read_affects_only_chosen_notice_and_no_holdings(self):
        self.enable(above_enabled=True,above=9,rise_enabled=True,rise=1)
        self.process(self.quote(11))
        records = self.records()
        self.store.mark_read(records[0]['id'])
        self.assertEqual(sum(r['is_read'] for r in self.records()),1)
        self.assertEqual(self.holdings.load().locked,200)

    def test_code_change_cannot_reuse_previous_code_thresholds(self):
        self.enable(above_enabled=True,above=9)
        changed = self.holdings.update_position(name='新代码',code='600000.SH',total=1200,cost=9,expected_version=1)
        self.assertFalse(self.process(self.quote(),position=changed)[1])
        self.assertFalse(self.store.records(changed.position_id,changed.code))

    def test_archived_stock_does_not_analyze_or_alert(self):
        self.enable(above_enabled=True,above=9)
        self.assertEqual(self.process(self.quote(),position=replace(self.position,archived=True)),(None,[]))
        self.assertFalse(self.records())

    def test_unknown_database_version_or_wrong_database_not_reset(self):
        before = self.holdings.path.read_bytes()
        with self.assertRaises(RiskError):
            RiskStore(self.holdings.path)
        self.assertEqual(self.holdings.path.read_bytes(),before)
        with closing(sqlite3.connect(self.store.path)) as db:
            db.execute('PRAGMA user_version=99')
        with self.assertRaises(RiskError):
            RiskStore(self.store.path)
        with closing(sqlite3.connect(self.store.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],99)

    def test_schema_failure_rolls_back_instead_of_making_empty_success(self):
        path = self.path/'failed.sqlite3'
        original = RiskStore._create_schema
        def fail(db):
            original(db)
            raise sqlite3.OperationalError('simulated')
        with patch.object(RiskStore,'_create_schema',side_effect=fail), self.assertRaises(RiskError):
            RiskStore(path)
        with closing(sqlite3.connect(path)) as db:
            self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        with self.assertRaises(RiskError):
            RiskStore(path)

    def test_locked_database_fails_safely_then_retry_works(self):
        with closing(sqlite3.connect(self.store.path)) as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(RiskError):
                self.process(self.quote())
        self.assertTrue(self.process(self.quote())[0])

    def test_repeated_checks_dont_duplicate_analysis_but_manual_record_allowed(self):
        self.process(self.quote())
        self.process(self.quote())
        self.assertEqual(len(self.store.records(analyses=True)),1)
        self.store.process(self.position,self.quote(),'sina',now=NOW,force_record=True)
        self.assertEqual(len(self.store.records(analyses=True)),2)

    def test_old_single_stock_and_multi_history_are_untouched(self):
        path=self.path/'legacy.sqlite3'
        make_v1(path,total=1875,cost='8.123456',locked=325)
        holdings=PositionStore(path)
        before=path.read_bytes()
        self.store.process(holdings.load(),self.quote(),'sina',now=NOW)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(holdings.load().locked,325)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM position_history').fetchone()[0],2)

    def test_unrelated_threshold_edit_does_not_rearm_stale_notification(self):
        self.enable(data_enabled=True,cooldown=60)
        self.process(self.quote(),now=NOW+121)
        self.enable(data_enabled=True,cooldown=60,above_enabled=True,above=9)
        self.assertFalse(self.process(self.quote(),now=NOW+600)[1])
        self.assertEqual(len(self.records()),1)

    def test_alert_insert_failure_rolls_back_analysis_state_and_record(self):
        self.enable(above_enabled=True,above=9)
        with closing(sqlite3.connect(self.store.path)) as db:
            db.execute("CREATE TRIGGER reject_notice BEFORE INSERT ON risk_records BEGIN SELECT RAISE(ABORT,'test'); END")
            db.commit()
        with self.assertRaises(RiskError):
            self.process(self.quote())
        self.assertFalse(self.records())
        self.assertFalse(self.store.records(analyses=True))
        with closing(sqlite3.connect(self.store.path)) as db:
            db.execute('DROP TRIGGER reject_notice')
            db.commit()
        self.assertEqual(len(self.process(self.quote())[1]),1)

    def test_corrupt_settings_not_silently_replaced_with_defaults(self):
        self.enable(above_enabled=True,above=9)
        with closing(sqlite3.connect(self.store.path)) as db:
            db.execute("UPDATE risk_settings SET options='null'")
            db.commit()
        with self.assertRaises(RiskError):
            self.process(self.quote())
        self.assertFalse(self.records())

    def test_risk_library_has_no_position_mutation_or_network_capability(self):
        import ast
        import stockwidget.core.risk as module
        tree=ast.parse(Path(module.__file__).read_text(encoding='utf-8-sig'))
        allowed={'contextlib','dataclasses','datetime','decimal','pathlib','json','sqlite3','stockwidget'}
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                self.assertTrue(all(alias.name.split('.')[0] in allowed for alias in node.names))
            elif isinstance(node,ast.ImportFrom):
                self.assertIn(node.module.split('.')[0],allowed)
                self.assertFalse({'PositionStore','PortfolioPanel'} & {alias.name for alias in node.names})
        with closing(sqlite3.connect(self.store.path)) as db:
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse({'position','positions','position_history'} & tables)

    def test_restart_before_first_quote_does_not_rearm_data_alert(self):
        self.enable(data_enabled=True,cooldown=60)
        self.process(self.quote(),now=NOW+121)
        self.store=RiskStore(self.store.path)
        self.assertFalse(self.process(None,now=NOW+86400)[1])
        self.assertFalse(self.process(None,now=NOW+86410,failures=1)[1])
        self.assertFalse(self.process(self.quote(),now=NOW+86460)[1])
        self.assertEqual(len(self.records()),1)
