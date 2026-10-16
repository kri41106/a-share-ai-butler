"""多股票迁移、备份、逐股隔离和汇总；全部使用测试数据/临时目录。"""
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sqlite3
import unittest

from stockwidget.core.portfolio import PositionStore, PortfolioError, portfolio_totals


def make_v1(path, *, total=1200, cost="9.429", locked=200):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE position(id INTEGER PRIMARY KEY CHECK(id=1),name TEXT NOT NULL,code TEXT NOT NULL,"
                   "total INTEGER NOT NULL,cost TEXT NOT NULL,locked INTEGER NOT NULL,version INTEGER NOT NULL,updated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE position_history(id INTEGER PRIMARY KEY,changed_at TEXT NOT NULL,reason TEXT NOT NULL,"
                   "before_json TEXT,after_json TEXT NOT NULL)")
        db.execute("INSERT INTO position VALUES (1,'博纳影业','001330.SZ',?,?,?,7,'2026-10-15T12:00:00+00:00')", (total,cost,locked))
        db.execute("INSERT INTO position_history VALUES (11,'first','首次初始化',NULL,'original JSON 1')")
        db.execute("INSERT INTO position_history VALUES (22,'second','用户修改','original JSON 1','original JSON 2')")
        db.execute("PRAGMA user_version=1")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.path = self.directory / "positions.sqlite3"

    def rows(self, path, query):
        with closing(sqlite3.connect(path)) as db:
            return db.execute(query).fetchall()

    def test_v1_data_and_history_preserved_with_backup(self):
        make_v1(self.path)
        history = self.rows(self.path,"SELECT * FROM position_history ORDER BY id")
        store = PositionStore(self.path)
        p = store.load()
        self.assertEqual((p.total,p.cost,p.locked,p.strategy,p.version), (1200,Decimal("9.429"),200,1000,7))
        self.assertEqual(self.rows(self.path,"SELECT id,changed_at,reason,before_json,after_json FROM position_history ORDER BY id"), history)
        self.assertEqual(self.rows(store.backup_path,"SELECT * FROM position_history ORDER BY id"),history)
        self.assertEqual(self.rows(store.backup_path,"PRAGMA user_version"),[(1,)])

    def test_real_edits_are_not_replaced_by_defaults(self):
        make_v1(self.path,total=1875,cost="8.123456",locked=325)
        p = PositionStore(self.path).load()
        self.assertEqual((p.total,p.cost,p.locked), (1875,Decimal("8.123456"),325))

    def test_migration_idempotent_no_extra_history_or_backups(self):
        make_v1(self.path)
        PositionStore(self.path)
        for _ in range(3):
            self.assertEqual(len(PositionStore(self.path).load_all()),1)
        self.assertEqual(len(list((self.directory/'backups').glob('*.sqlite3'))),1)
        self.assertEqual(self.rows(self.path,"SELECT count(*) FROM position_history"),[(2,)])

    def test_failed_migration_rolls_back_schema_and_values(self):
        make_v1(self.path,total=1777)
        original = PositionStore._migrate_v1
        def fail(store,db):
            original(store,db)
            raise sqlite3.OperationalError("test migration failure")
        with patch.object(PositionStore,"_migrate_v1",fail), self.assertRaises(PortfolioError):
            PositionStore(self.path)
        self.assertEqual(self.rows(self.path,"PRAGMA user_version"),[(1,)])
        self.assertEqual(self.rows(self.path,"SELECT total FROM position"),[(1777,)])
        self.assertEqual(self.rows(self.path,"SELECT name FROM sqlite_master WHERE name='positions'"),[])
        self.assertEqual(PositionStore(self.path).load().total,1777)

    def test_backup_failure_prevents_migration(self):
        make_v1(self.path)
        with patch.object(PositionStore,"_backup_v1",side_effect=OSError("disk full")), self.assertRaises(PortfolioError):
            PositionStore(self.path)
        self.assertEqual(self.rows(self.path,"PRAGMA user_version"),[(1,)])
        self.assertEqual(self.rows(self.path,"SELECT count(*) FROM position_history"),[(2,)])

    def test_missing_v1_record_does_not_initialize_defaults(self):
        make_v1(self.path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM position")
        with self.assertRaises(PortfolioError):
            PositionStore(self.path)
        self.assertEqual(self.rows(self.path,"PRAGMA user_version"),[(1,)])

    def test_backup_contains_committed_wal_changes(self):
        make_v1(self.path)
        with closing(sqlite3.connect(self.path)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("UPDATE position SET total=1666")
            writer.commit()
            store = PositionStore(self.path)
            self.assertEqual(self.rows(store.backup_path,"SELECT total FROM position"),[(1666,)])


class MultiPositionTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.store = PositionStore(Path(directory)/'positions.sqlite3')
        self.second = self.store.add_position(name="浦发银行",code="600000.SH",total=500,cost="10")

    def test_add_second_has_zero_lock(self):
        self.assertEqual(len(self.store.load_all()),2)
        self.assertEqual((self.second.locked,self.second.strategy),(0,500))

    def test_duplicate_stock_is_rejected(self):
        with self.assertRaisesRegex(PortfolioError,"代码已存在"):
            self.store.add_position(name="重复",code="600000.sh",total=1,cost=1)

    def test_separate_edits_and_history_are_bound_to_id(self):
        first = self.store.load()
        self.store.update_position(position_id=1,name=first.name,code=first.code,total=1300,cost="9",expected_version=first.version)
        second = self.store.update_position(position_id=self.second.position_id,name=self.second.name,code=self.second.code,total=600,cost="11",expected_version=self.second.version)
        self.assertEqual((self.store.load().total,second.total),(1300,600))
        with closing(sqlite3.connect(self.store.path)) as db:
            rows = db.execute("SELECT DISTINCT position_id FROM position_history").fetchall()
        self.assertEqual(set(rows),{(1,),(self.second.position_id,)})

    def test_lock_changes_do_not_touch_other_stock(self):
        ticket = self.store.begin_lock_change(100,position_id=self.second.position_id)
        self.store.confirm_lock_change(ticket,confirmed=True)
        self.assertEqual((self.store.load().locked,self.store.load(self.second.position_id).locked),(200,100))

    def test_sell_stock_identity_and_quantity_are_checked(self):
        self.assertEqual(self.store.validate_sell(1,"001330.SZ",1000),1000)
        with self.assertRaises(PortfolioError):
            self.store.validate_sell(self.second.position_id,"001330.SZ",500)
        with self.assertRaises(PortfolioError):
            self.store.validate_sell(self.second.position_id,"600000.SH",501)

    def test_archive_preserves_values_and_other_stock(self):
        before = self.store.load()
        with self.assertRaises(PortfolioError):
            self.store.set_archived(self.second.position_id,expected_version=self.second.version)
        archived = self.store.set_archived(self.second.position_id,expected_version=self.second.version,confirmed=True)
        self.assertEqual(self.store.load(),before)
        self.assertEqual(len(self.store.load_all()),1)
        self.assertTrue(archived.archived)
        self.assertEqual((archived.total,archived.cost),(500,Decimal("10")))
        with self.assertRaises(PortfolioError):
            self.store.validate_sell(archived.position_id,archived.code,1)

    def test_restore_keeps_original_lock_and_cost(self):
        archived = self.store.set_archived(1,expected_version=self.store.load().version,confirmed=True)
        restored = self.store.set_archived(1,expected_version=archived.version,archived=False,confirmed=True)
        self.assertEqual((restored.locked,restored.cost),(200,Decimal("9.429")))

    def test_archived_code_cannot_be_added_again(self):
        self.store.set_archived(1,expected_version=1,confirmed=True)
        with self.assertRaises(PortfolioError):
            self.store.add_position(name="重复",code="001330.SZ",total=0,cost=0)

    def test_restart_retains_all_active_and_archived_positions(self):
        self.store.set_archived(1,expected_version=1,confirmed=True)
        loaded = PositionStore(self.store.path)
        self.assertEqual(len(loaded.load_all()),1)
        self.assertEqual(len(loaded.load_all(include_archived=True)),2)

    def test_archiving_all_does_not_reseed_default(self):
        for p in self.store.load_all():
            self.store.set_archived(p.position_id,expected_version=p.version,confirmed=True)
        self.assertEqual(PositionStore(self.store.path).load_all(),[])

    def test_total_value_profit_and_partial_coverage(self):
        positions = self.store.load_all()
        total = portfolio_totals(positions,{"001330.SZ":{"current_price":10},"600000.SH":{"current_price":12}})
        self.assertEqual(total,{"value":Decimal("18000"),"profit":Decimal("1685.200"),"covered":2,"total":2})
        partial = portfolio_totals(positions,{"001330.SZ":{"current_price":10}})
        self.assertEqual((partial['covered'],partial['total'],partial['value']),(1,2,Decimal("12000")))
        self.assertIsNone(portfolio_totals(positions,{})['value'])

    def test_duplicate_code_update_rolls_back_both_positions(self):
        first = self.store.load()
        with self.assertRaises(PortfolioError):
            self.store.update_position(position_id=1,name=first.name,code=self.second.code,total=first.total,cost=first.cost,expected_version=first.version)
        self.assertEqual(self.store.load(),first)
        self.assertEqual(self.store.load(self.second.position_id),self.second)
