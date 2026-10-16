"""本地多股票持仓与锁定仓保护。无网络、模型或真实交易能力。

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
    position_id: int = 1
    archived: bool = False

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


def validate_sell_quantity(position, quantity, *, code=None):
    """将来每条卖出数量建议都必须调用此入口；只校验，不执行交易。"""
    validate_position(position)
    if position.archived or (code is not None and a_share_code(code) != position.code):
        raise PortfolioError("股票不匹配或已归档，已拦截卖出数量建议。")
    quantity = shares(quantity)
    if quantity > position.strategy:
        raise PortfolioError("建议数量超过策略仓，已拦截；锁定仓不得纳入卖出建议。")
    return quantity


def portfolio_totals(positions, quotes):
    """部分覆盖的估值明确返回 covered/total；零覆盖返回 None 而不是0元。"""
    value = profit = Decimal(0)
    covered = 0
    for position in positions:
        result = valuation(position, (quotes.get(position.code) or {}).get("current_price"))
        if result is not None:
            value += result[0]
            profit += result[1]
            covered += 1
    return {"value": value if covered else None, "profit": profit if covered else None,
            "covered": covered, "total": len(positions)}


class PositionStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else Path(config_store.config_paths()) / "portfolio" / "positions.sqlite3"
        self._pending = None
        self.backup_path = None
        existed = self.path.exists()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._db() as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    if existed or db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                        raise PortfolioError("持仓数据库为空或版本不明，未新建示例数据，请检查备份。")
                    self._create_positions(db)
                    db.execute("CREATE TABLE position_history (id INTEGER PRIMARY KEY, changed_at TEXT NOT NULL, "
                               "reason TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL, "
                               "position_id INTEGER NOT NULL)")
                    initial = Position("博纳影业", "001330.SZ", 1200, Decimal("9.429"), 200,
                                       updated_at=self._now())
                    db.execute("INSERT INTO positions VALUES (1,?,?,?,?,?,?,?,0)", self._values(initial))
                    self._history(db, None, initial, "首次初始化")
                    db.execute("PRAGMA user_version=2")
                elif version == 1:
                    self.backup_path = self._backup_v1()
                    self._migrate_v1(db)
                elif version != 2:
                    raise PortfolioError("持仓数据库版本不支持，未修改数据。")
                for row in db.execute("SELECT * FROM positions"):
                    self._decode(row)
                db.execute("SELECT position_id FROM position_history LIMIT 1")
                db.execute("CREATE INDEX IF NOT EXISTS history_by_position ON position_history(position_id,id)")
        except OSError:
            raise PortfolioError("本地持仓目录或备份不可写，原数据未重置。请检查权限与磁盘空间。") from None

    @staticmethod
    def _create_positions(db):
        db.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, name TEXT NOT NULL, code TEXT NOT NULL UNIQUE, "
                   "total INTEGER NOT NULL CHECK(total>=0), cost TEXT NOT NULL, "
                   "locked INTEGER NOT NULL CHECK(locked>=0 AND locked<=total), "
                   "version INTEGER NOT NULL, updated_at TEXT NOT NULL, "
                   "archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)))")

    def _backup_v1(self):
        """在迁移写锁内，从独立只读连接备份已提交快照（含 WAL）。"""
        directory = self.path.parent / "backups"
        directory.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = directory / f"positions.pre-multi-v1-{stamp}-{secrets.token_hex(4)}.sqlite3"
        partial = backup.with_suffix(".partial")
        try:
            with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as source:
                with closing(sqlite3.connect(partial)) as target:
                    source.backup(target)
                    if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise PortfolioError("升级前备份校验失败，原数据库未改动。")
            partial.replace(backup)
        except (OSError, sqlite3.Error):
            raise PortfolioError("升级前数据库备份失败，迁移已取消；原数据库未改动。") from None
        return backup

    def _migrate_v1(self, db):
        legacy = db.execute("SELECT * FROM position WHERE id=1").fetchone()
        if legacy is None:
            raise PortfolioError("旧持仓记录缺失，迁移已取消，没有用默认值覆盖。")
        position = self._decode(legacy)
        self._create_positions(db)
        db.execute("INSERT INTO positions VALUES (1,?,?,?,?,?,?,?,0)", self._values(position))
        db.execute("ALTER TABLE position_history ADD COLUMN position_id INTEGER NOT NULL DEFAULT 1")
        # 原 position 表及历史 JSON 保留；新版本只使用 positions 表。
        db.execute("PRAGMA user_version=2")

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
        except sqlite3.IntegrityError:
            raise PortfolioError("该股票代码已存在（包括归档记录），请修改已有持仓或恢复归档记录。") from None
        except sqlite3.Error:
            raise PortfolioError("本地持仓数据库暂不可用，操作已回滚，数据未重置。请检查文件权限、占用和磁盘空间。") from None

    @staticmethod
    def _values(position):
        return (position.name, position.code, position.total, str(position.cost),
                position.locked, position.version, position.updated_at)

    @staticmethod
    def _decode(row):
        try:
            position = Position(row["name"], row["code"], row["total"], money(row["cost"]),
                                row["locked"], row["version"], row["updated_at"], row["id"],
                                bool(row["archived"]) if "archived" in row.keys() else False)
            validate_position(position)
            return position
        except (KeyError, TypeError, PortfolioError):
            raise PortfolioError("持仓记录校验失败，未修改数据，请检查备份。") from None

    def _read(self, db, position_id=1, *, include_archived=False):
        row = db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
        if row is None or (row["archived"] and not include_archived):
            raise PortfolioError("持仓记录不存在或已归档，请刷新持仓列表。")
        return self._decode(row)

    @staticmethod
    def _history(db, before, after, reason):
        def encode(value):
            return json.dumps(asdict(value), ensure_ascii=False, default=str) if value else None
        db.execute("INSERT INTO position_history(changed_at,reason,before_json,after_json,position_id) VALUES (?,?,?,?,?)",
                   (after.updated_at, reason, encode(before), encode(after), after.position_id))

    def load(self, position_id=1, *, include_archived=False):
        with self._db() as db:
            return self._read(db, position_id, include_archived=include_archived)

    def load_all(self, *, include_archived=False):
        with self._db() as db:
            query = "SELECT * FROM positions" + ("" if include_archived else " WHERE archived=0") + " ORDER BY id"
            return [self._decode(row) for row in db.execute(query)]

    def _save(self, db, before, after, reason):
        validate_position(after)
        db.execute("UPDATE positions SET name=?,code=?,total=?,cost=?,locked=?,version=?,updated_at=?,archived=? WHERE id=?",
                   (*self._values(after), int(after.archived), after.position_id))
        self._history(db, before, after, reason)
        return after

    def add_position(self, *, name, code, total, cost):
        # 新股票的锁定仓只能为0；普通添加接口不接受 locked。
        new = Position(name.strip() if isinstance(name,str) else name, a_share_code(code), shares(total), money(cost), 0, updated_at=self._now())
        validate_position(new)
        with self._db() as db:
            cursor = db.execute("INSERT INTO positions(name,code,total,cost,locked,version,updated_at,archived) "
                                "VALUES (?,?,?,?,?,?,?,0)", self._values(new))
            new = Position(new.name, new.code, new.total, new.cost, 0, 1, new.updated_at, cursor.lastrowid)
            self._history(db, None, new, "用户添加股票")
            return new

    def update_position(self, *, name, code, total, cost, expected_version, position_id=1):
        code, total, cost = a_share_code(code), shares(total), money(cost)
        with self._db() as db:
            current = self._read(db, position_id)
            if expected_version != current.version:
                raise PortfolioError("持仓已发生变化，请重新打开修改页面。")
            new = Position(name.strip() if isinstance(name,str) else name, code, total, cost, current.locked,
                           current.version + 1, self._now(), current.position_id)
            return self._save(db, current, new, "用户修改持仓")

    def begin_lock_change(self, quantity, *, position_id=1, expected_version=None):
        current = self.load(position_id)
        if expected_version is not None and current.version != expected_version:
            raise PortfolioError("持仓已发生变化，请重新打开锁定仓设置。")
        quantity = shares(quantity)
        if quantity > current.total:
            raise PortfolioError("锁定仓不能超过总持仓。")
        ticket = secrets.token_urlsafe(24)
        self._pending = (ticket, current.position_id, current.version, quantity)
        return ticket

    def confirm_lock_change(self, ticket, *, confirmed=False):
        pending, self._pending = self._pending, None
        if confirmed is not True or pending is None or ticket != pending[0]:
            raise PortfolioError("锁定仓修改未完成二次确认，未保存。")
        with self._db() as db:
            current = self._read(db, pending[1])
            if current.version != pending[2]:
                raise PortfolioError("持仓已发生变化，请重新确认锁定仓。")
            new = Position(current.name, current.code, current.total, current.cost, pending[3],
                           current.version + 1, self._now(), current.position_id)
            return self._save(db, current, new, "用户二次确认修改锁定仓")

    def set_archived(self, position_id, *, expected_version, archived=True, confirmed=False):
        if confirmed is not True:
            raise PortfolioError("归档或恢复需要二次确认，未修改记录。")
        with self._db() as db:
            current = self._read(db, position_id, include_archived=True)
            if current.version != expected_version:
                raise PortfolioError("持仓已发生变化，请重新确认。")
            new = Position(current.name, current.code, current.total, current.cost, current.locked,
                           current.version + 1, self._now(), current.position_id, bool(archived))
            return self._save(db, current, new, "用户归档股票" if archived else "用户恢复归档股票")

    def validate_sell(self, position_id, code, quantity):
        # 将来的每条建议必须带持仓ID和股票代码，读取最新仓位后独立验证。
        return validate_sell_quantity(self.load(position_id), quantity, code=code)
