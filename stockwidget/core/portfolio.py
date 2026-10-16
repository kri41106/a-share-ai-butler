"""本地单股票持仓与锁定仓保护。无网络、模型或真实交易能力。

新增于 2026-10-16，a-share-ai-butler，Apache-2.0。
"""
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
import json
import re
import secrets
import sqlite3

from stockwidget.core import config_store


class PortfolioError(ValueError):
    """可安全显示的中文业务错误（不包含个人持仓或数据库路径）。"""


def shares(value):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise PortfolioError("股数必须是非负整数。")
    number = int(value)
    if number > 1_000_000_000:
        raise PortfolioError("股数超出支持范围。")
    return number


def money(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise PortfolioError("价格必须是有效的非负数字。") from None
    if not result.is_finite() or result < 0 or result > Decimal("1000000"):
        raise PortfolioError("价格必须在 0 至 1000000 元之间。")
    if result.as_tuple().exponent < -6:
        raise PortfolioError("价格最多保留六位小数。")
    return result


def a_share_code(value):
    code = str(value).strip().upper()
    patterns = {
        "SH": r"(?:600|601|603|605|688|689)[0-9]{3}",
        "SZ": r"(?:000|001|002|003|300|301)[0-9]{3}",
        "BJ": r"(?:(?:43|83|87)[0-9]{4}|920[0-9]{3})",
    }
    match = re.fullmatch(r"([0-9]{6})\.(SH|SZ|BJ)", code)
    if not match or not re.fullmatch(patterns[match[2]], match[1]):
        raise PortfolioError("请输入沪深北 A 股代码，例如 001330.SZ、600000.SH、920001.BJ。")
    return code


@dataclass(frozen=True)
class Position:
    name: str
    code: str
    total: int
    cost: Decimal
    locked: int
    version: int = 1
    updated_at: str = ""

    @property
    def strategy(self):
        return self.total - self.locked

    def instrument(self):
        code, market = self.code.split(".")
        return {"code": code, "market": market.lower(), "name": self.name,
                "type": {"SH": "沪", "SZ": "深", "BJ": "京"}[market], "checked": True}


def validate_position(position):
    if not isinstance(position.name, str) or not position.name.strip() or len(position.name.strip()) > 40:
        raise PortfolioError("股票名称不能为空，且不能超过40个字。")
    a_share_code(position.code)
    shares(position.total)
    shares(position.locked)
    money(position.cost)
    if position.locked > position.total:
        raise PortfolioError("总持仓不能低于锁定仓，请先在专门的锁定仓设置中调整。")


def valuation(position, price):
    """无有效价格时返回 None；成本为0时百分比无法计算。"""
    validate_position(position)
    try:
        price = money(price)
    except PortfolioError:
        return None
    if price <= 0:
        return None
    with localcontext() as context:
        context.prec = 40
        return (price * position.total, (price - position.cost) * position.total,
                (price - position.cost) / position.cost * 100 if position.cost else None)


def validate_sell_quantity(position, quantity):
    """将来每条卖出数量建议都必须调用此入口；只校验，不执行交易。"""
    validate_position(position)
    quantity = shares(quantity)
    if quantity > position.strategy:
        raise PortfolioError("建议数量超过策略仓，已拦截；锁定仓不得纳入卖出建议。")
    return quantity


class PositionStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else Path(config_store.config_paths()) / "portfolio" / "positions.sqlite3"
        self._pending = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._db() as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                        raise PortfolioError("持仓数据库版本不明，未修改数据，请备份后检查。")
                    db.execute("CREATE TABLE position (id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL, "
                               "code TEXT NOT NULL, total INTEGER NOT NULL CHECK(total>=0), cost TEXT NOT NULL, "
                               "locked INTEGER NOT NULL CHECK(locked>=0 AND locked<=total), "
                               "version INTEGER NOT NULL, updated_at TEXT NOT NULL)")
                    db.execute("CREATE TABLE position_history (id INTEGER PRIMARY KEY, changed_at TEXT NOT NULL, "
                               "reason TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL)")
                    initial = Position("博纳影业", "001330.SZ", 1200, Decimal("9.429"), 200,
                                       updated_at=self._now())
                    db.execute("INSERT INTO position VALUES (1,?,?,?,?,?,?,?)", self._values(initial))
                    self._history(db, None, initial, "首次初始化")
                    db.execute("PRAGMA user_version=1")
                elif version != 1:
                    raise PortfolioError("持仓数据库版本不支持，未修改数据。")
                self._read(db)
        except OSError:
            raise PortfolioError("无法创建本地持仓目录，请检查权限和磁盘空间。") from None

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @contextmanager
    def _db(self):
        try:
            with closing(sqlite3.connect(self.path, timeout=3)) as db:
                db.row_factory = sqlite3.Row
                db.execute("BEGIN IMMEDIATE")
                with db:
                    yield db
        except sqlite3.Error:
            raise PortfolioError("本地持仓数据库暂不可用，数据未重置。请检查文件权限、占用和磁盘空间。") from None

    @staticmethod
    def _values(position):
        return (position.name, position.code, position.total, str(position.cost),
                position.locked, position.version, position.updated_at)

    @staticmethod
    def _read(db):
        row = db.execute("SELECT * FROM position WHERE id=1").fetchone()
        if row is None:
            raise PortfolioError("持仓记录缺失，未用示例数据覆盖，请检查备份。")
        try:
            position = Position(row["name"], row["code"], row["total"], money(row["cost"]),
                                row["locked"], row["version"], row["updated_at"])
            validate_position(position)
            return position
        except (KeyError, TypeError, PortfolioError):
            raise PortfolioError("持仓记录校验失败，未修改数据，请检查备份。") from None

    @staticmethod
    def _history(db, before, after, reason):
        def encode(value):
            return json.dumps(asdict(value), ensure_ascii=False, default=str) if value else None
        db.execute("INSERT INTO position_history(changed_at,reason,before_json,after_json) VALUES (?,?,?,?)",
                   (after.updated_at, reason, encode(before), encode(after)))

    def load(self):
        with self._db() as db:
            return self._read(db)

    def _save(self, db, before, after, reason):
        validate_position(after)
        db.execute("UPDATE position SET name=?,code=?,total=?,cost=?,locked=?,version=?,updated_at=? WHERE id=1",
                   self._values(after))
        self._history(db, before, after, reason)
        return after

    def update_position(self, *, name, code, total, cost, expected_version):
        """普通编辑不接收 locked 参数；在事务内读取并原样保留锁定仓。"""
        code, total, cost = a_share_code(code), shares(total), money(cost)
        with self._db() as db:
            current = self._read(db)
            if expected_version != current.version:
                raise PortfolioError("持仓已发生变化，请重新打开修改页面。")
            new = Position(name.strip(), code, total, cost, current.locked,
                           current.version + 1, self._now())
            return self._save(db, current, new, "用户修改持仓")

    def begin_lock_change(self, quantity):
        """仅供专门设置页：生成一次性、与当前版本绑定的确认请求。"""
        current = self.load()
        quantity = shares(quantity)
        if quantity > current.total:
            raise PortfolioError("锁定仓不能超过总持仓。")
        ticket = secrets.token_urlsafe(24)
        self._pending = (ticket, current.version, quantity)
        return ticket

    def confirm_lock_change(self, ticket, *, confirmed=False):
        pending, self._pending = self._pending, None
        if confirmed is not True or pending is None or ticket != pending[0]:
            raise PortfolioError("锁定仓修改未完成二次确认，未保存。")
        with self._db() as db:
            current = self._read(db)
            if current.version != pending[1]:
                raise PortfolioError("持仓已发生变化，请重新确认锁定仓。")
            new = Position(current.name, current.code, current.total, current.cost, pending[2],
                           current.version + 1, self._now())
            return self._save(db, current, new, "用户二次确认修改锁定仓")
