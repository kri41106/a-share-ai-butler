"""本地持仓与独立保护边界；仅使用临时数据库，无真实交易。"""
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import ast
import sqlite3
import unittest

from stockwidget.core.portfolio import (
    PositionStore, PortfolioError, a_share_code, valuation, validate_sell_quantity,
)


class PositionTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(TemporaryDirectory())
        self.path = Path(self.directory) / "portfolio" / "positions.sqlite3"
        self.store = PositionStore(self.path)

    def change(self, **fields):
        p = self.store.load()
        values = dict(name=p.name, code=p.code, total=p.total, cost=p.cost, expected_version=p.version)
        values.update(fields)
        return self.store.update_position(**values)

    def count_history(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT count(*) FROM position_history").fetchone()[0]

    def test_initial_position(self):
        p = self.store.load()
        self.assertEqual((p.name, p.code, p.total, p.cost, p.locked, p.strategy),
                         ("博纳影业", "001330.SZ", 1200, Decimal("9.429"), 200, 1000))
        self.assertTrue(p.updated_at)
        self.assertEqual(self.count_history(), 1)

    def test_restart_retains_edits(self):
        self.change(total=1300, cost="8.123456")
        p = PositionStore(self.path).load()
        self.assertEqual((p.total, p.cost, p.locked), (1300, Decimal("8.123456"), 200))

    def test_initialization_is_idempotent(self):
        self.change(total=300, cost="10")
        for _ in range(3):
            PositionStore(self.path)
        self.assertEqual(self.store.load().total, 300)
        self.assertEqual(self.count_history(), 2)

    def test_calculations_use_decimal(self):
        value, profit, pct = valuation(self.store.load(), "10")
        self.assertEqual(value, Decimal("12000"))
        self.assertEqual(profit, Decimal("685.200"))
        self.assertAlmostEqual(pct, Decimal("6.055785343090465584897656167"))

    def test_edit_cost_recalculates(self):
        p = self.change(cost="10")
        self.assertEqual(valuation(p, "9")[1], Decimal("-1200"))

    def test_zero_cost_does_not_divide_by_zero(self):
        self.assertIsNone(valuation(self.change(cost="0"), "5")[2])

    def test_missing_or_invalid_price_has_no_valuation(self):
        for price in (None, 0, -1, "nan", "inf", "bad", True):
            with self.subTest(price=price):
                self.assertIsNone(valuation(self.store.load(), price))

    def test_strategy_calculation(self):
        self.assertEqual(self.change(total=450).strategy, 250)

    def test_total_below_locked_is_blocked_without_history(self):
        before = self.store.load()
        with self.assertRaisesRegex(PortfolioError, "总持仓不能低于锁定仓"):
            self.change(total=199)
        self.assertEqual(self.store.load(), before)
        self.assertEqual(self.count_history(), 1)

    def test_invalid_shares_rejected(self):
        for total in (-1, "1.5", "1200.0", "", True, 1.5, "1e3", 1_000_000_001):
            with self.subTest(total=total), self.assertRaises(PortfolioError):
                self.change(total=total)

    def test_invalid_cost_rejected(self):
        for cost in ("-0.1", "nan", "inf", "bad", "0.0000001", True):
            with self.subTest(cost=cost), self.assertRaises(PortfolioError):
                self.change(cost=cost)

    def test_invalid_stock_codes_rejected(self):
        for code in ("AAPL", "00700.HK", "510300.SH", "600000.SZ", "000001.SH", "123456.SZ", "", "60000.SH"):
            with self.subTest(code=code), self.assertRaises(PortfolioError):
                self.change(code=code)

    def test_sh_sz_bj_code_formats(self):
        for code in ("001330.SZ", "600000.SH", "688001.SH", "920001.BJ", "830799.BJ"):
            self.assertEqual(a_share_code(code.lower()), code)

    def test_name_required(self):
        with self.assertRaises(PortfolioError):
            self.change(name=" ")

    def test_regular_edit_has_no_locked_parameter(self):
        with self.assertRaises(TypeError):
            self.change(locked=0)
        self.assertEqual(self.store.load().locked, 200)

    def test_stock_change_preserves_lock(self):
        self.assertEqual(self.change(code="600000.SH", name="浦发银行").locked, 200)

    def test_lock_change_requires_second_confirmation(self):
        ticket = self.store.begin_lock_change(100)
        with self.assertRaises(PortfolioError):
            self.store.confirm_lock_change(ticket)
        self.assertEqual(self.store.load().locked, 200)
        with self.assertRaises(PortfolioError):
            self.store.confirm_lock_change(ticket, confirmed=True)

    def test_lock_change_requires_valid_ticket(self):
        with self.assertRaises(PortfolioError):
            self.store.confirm_lock_change("invented", confirmed=True)

    def test_lock_change_is_persisted_and_ticket_cannot_replay(self):
        ticket = self.store.begin_lock_change(100)
        self.store.confirm_lock_change(ticket, confirmed=True)
        self.assertEqual(PositionStore(self.path).load().locked, 100)
        self.assertEqual(self.count_history(), 2)
        with self.assertRaises(PortfolioError):
            self.store.confirm_lock_change(ticket, confirmed=True)

    def test_lock_above_total_rejected(self):
        with self.assertRaises(PortfolioError):
            self.store.begin_lock_change(1201)

    def test_stale_lock_confirmation_rejected(self):
        ticket = self.store.begin_lock_change(100)
        self.change(total=1300)
        with self.assertRaises(PortfolioError):
            self.store.confirm_lock_change(ticket, confirmed=True)
        self.assertEqual(self.store.load().locked, 200)

    def test_stale_editor_rejected(self):
        p = self.store.load()
        self.change(total=1300)
        with self.assertRaises(PortfolioError):
            self.change(total=1400, expected_version=p.version)

    def test_sell_never_uses_locked_shares(self):
        p = self.store.load()
        self.assertEqual(validate_sell_quantity(p, 1000), 1000)
        for quantity in (1001, 1200, -1, "1.1"):
            with self.subTest(quantity=quantity), self.assertRaises(PortfolioError):
                validate_sell_quantity(p, quantity)

    def test_zero_strategy_only_allows_zero_sell(self):
        p = self.change(total=200)
        self.assertEqual(validate_sell_quantity(p, 0), 0)
        with self.assertRaises(PortfolioError):
            validate_sell_quantity(p, 1)

    def test_zero_total_after_explicit_unlock(self):
        ticket = self.store.begin_lock_change(0)
        self.store.confirm_lock_change(ticket, confirmed=True)
        self.assertEqual(self.change(total=0, cost=0).strategy, 0)

    def test_history_failure_rolls_back_position(self):
        before = self.store.load()
        with patch.object(self.store, "_history", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(PortfolioError):
                self.change(total=1300)
        self.assertEqual(self.store.load(), before)
        self.assertEqual(self.count_history(), 1)

    def test_corrupt_database_is_not_overwritten(self):
        self.path.write_bytes(b"not sqlite")
        with self.assertRaises(PortfolioError):
            PositionStore(self.path)
        self.assertEqual(self.path.read_bytes(), b"not sqlite")

    def test_unknown_schema_is_not_reinitialized(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaises(PortfolioError):
            PositionStore(self.path)

    def test_missing_table_not_reseeded(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DROP TABLE positions")
        with self.assertRaises(PortfolioError):
            PositionStore(self.path)

    def test_independent_database_leaves_history_cache_intact(self):
        cache = Path(self.directory) / "cache" / "history.sqlite3"
        cache.parent.mkdir()
        cache.write_bytes(b"original cache")
        self.change(total=1600)
        self.assertEqual(cache.read_bytes(), b"original cache")

    def test_business_module_cannot_network_or_trade(self):
        import stockwidget.core.portfolio as module
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        allowed = {"contextlib", "dataclasses", "datetime", "decimal", "pathlib", "json", "re", "secrets", "sqlite3", "stockwidget"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue(all(alias.name.split(".")[0] in allowed for alias in node.names))
            if isinstance(node, ast.ImportFrom):
                self.assertIn(node.module.split(".")[0], allowed)
        root = Path(module.__file__).parents[1]
        forbidden = {"xtquant", "easytrader", "easyquotation_trade", "pyautogui", "selenium", "playwright"}
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                if isinstance(node, ast.Import):
                    self.assertTrue(forbidden.isdisjoint(alias.name.split(".")[0] for alias in node.names))
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0], forbidden)
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, {"place_order", "submit_order", "cancel_order", "order_stock", "buy_stock", "sell_stock"})
