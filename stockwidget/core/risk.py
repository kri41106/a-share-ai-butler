"""本地规则分析与持久提醒；只接收只读持仓快照，无交易或网络能力。
Added 2026-10-17 for a-share-ai-butler; Apache-2.0.
"""
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import json
import sqlite3

from stockwidget.core.portfolio import PortfolioError, money, shares
from stockwidget.core.window_rules import quote_timestamp, QUOTE_TIMEZONE

KINDS = {'above': '股价高于', 'below': '股价低于', 'rise': '当日涨幅达到',
         'fall': '当日跌幅达到', 'data': '行情连续失败或过期'}
SOURCES = {'sina': '新浪财经', 'eastmoney': '东方财富'}
APP_ID = 1095975506


class RiskError(ValueError):
    """不含个人信息的本地提醒错误。"""


def settings(values=None):
    """空阈值保持关闭；不预设止盈止损价格。"""
    values = {} if values is None else values
    if not isinstance(values,dict):
        raise RiskError('提醒设置格式无效。')
    result = {}
    try:
        for kind in KINDS:
            enabled = values.get(kind + '_enabled', False)
            if not isinstance(enabled, bool):
                raise RiskError('启用状态无效。')
            result[kind + '_enabled'] = enabled
            if kind != 'data':
                value = values.get(kind)
                if value in (None, ''):
                    if enabled:
                        raise RiskError('请填写已启用条件的正数阈值。')
                    result[kind] = None
                else:
                    number = money(value)
                    if number <= 0 or (kind in ('rise', 'fall') and number > 1000):
                        raise RiskError('价格阈值必须大于0；百分比阈值须大于0且不超过1000%。')
                    result[kind] = str(number)
        for key, default, low, high in (('failures', 3, 1, 1000), ('stale_seconds', 120, 120, 86400),
                                        ('cooldown', 1800, 60, 604800)):
            number = shares(values.get(key, default))
            if not low <= number <= high:
                raise RiskError('连续失败次数、过期秒数或冷却秒数超出范围。')
            result[key] = number
    except PortfolioError:
        raise RiskError('提醒阈值必须为有效数字，次数和秒数必须为整数。') from None
    return result


def clock_text(timestamp):
    return datetime.fromtimestamp(timestamp, QUOTE_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S') + '（北京时间）'


@dataclass(frozen=True)
class Analysis:
    title: str
    text: str
    conditions: dict  # True=满足，False=恢复，None=数据不足，不改变价格提醒状态。
    quote_time: str
    source: str
    signature: str


def analyze(position, quote, source, options=None, *, failures=0, now):
    """描述已知数据，不预测走势，不产生买卖股数建议。"""
    options = settings(options)
    quote = quote or {}
    timestamp = quote_timestamp(quote)
    source_name = SOURCES.get(source, '未知来源')
    quote_time = clock_text(timestamp) if timestamp is not None else '未知'
    try:
        price = money(quote.get('current_price'))
        if price <= 0:
            price = None
    except PortfolioError:
        price = None
    age = now - timestamp if timestamp is not None else None
    fresh = price is not None and age is not None and -60 <= age <= 120 and failures == 0
    conditions = {kind: None for kind in KINDS}
    # 无行情时需达到连续失败次数；有行情但时间未知/异常不允许价格判断。
    bad = failures >= options['failures'] or (
        bool(quote) and (age is None or age < -60 or age > options['stale_seconds'] or price is None))
    conditions['data'] = True if bad else False if fresh else None  # 启动尚无行情不等于已恢复。
    lines = []
    if not fresh:
        if failures:
            title = '行情更新失败，暂不作出新的价格判断'
        elif age is not None and age > 120:
            title = '行情已过期，暂不作出新的价格判断'
        else:
            title = '数据不足或时间异常，无法判断'
        lines.append(f'依据：连续更新失败 {failures} 次；价格判断仅使用120秒内且时间正常的有效行情。')
        lines.append('当前价格：暂无有效行情；上次可得数据仅用于核对，不代表当前成交价。')
    else:
        lines.append(f'依据：可得价格 {price} 元，手动记录成本 {position.cost} 元/股。')
        lines.append('当前价格低于持仓成本。' if price < position.cost else '当前价格未低于持仓成本，不代表没有风险。')
        conditions['above'] = price > Decimal(options['above']) if options['above'] else False
        conditions['below'] = price < Decimal(options['below']) if options['below'] else False
        try:
            previous = money(quote.get('prev_close'))
            change = (price - previous) / previous * 100 if previous > 0 else None
        except PortfolioError:
            change = None
        if change is None:
            lines.append('昨收价缺失，无法计算当日涨跌幅。')
        else:
            lines.append(f'当日涨跌幅 {change:+.2f}%（现价与昨收价比较）。')
            conditions['rise'] = change >= Decimal(options['rise']) if options['rise'] else False
            conditions['fall'] = -change >= Decimal(options['fall']) if options['fall'] else False
        hits = [KINDS[k] + f'用户阈值 {options[k]}' + ('%' if k in ('rise','fall') else '元')
                for k in ('above','below','rise','fall') if options[k + '_enabled'] and conditions[k]]
        if hits:
            lines.append('已达到提醒条件：' + '；'.join(hits) + '。')
        title = '已触发用户设置的波动条件，请留意' if hits else (
            '当前价格低于持仓成本，请留意持仓风险' if price < position.cost else '未触发已启用的价格条件，仍需留意风险')
    lines.append(f'本地持仓 {position.total} 股；锁定仓 {position.locked} 股；策略仓 {position.strategy} 股。')
    lines.append(f'参考信息：{source_name}；行情时间 {quote_time}。')
    lines.append('风险与不确定性：仅为本地规则判断，非AI模型；行情可能延迟或出错，不含新闻或未来走势判断。'
                 '不保证收益，不构成买卖指令；真实交易由本人手动完成。')
    text = '当前看法：' + title + '\n\n' + '\n'.join(lines)
    signature = json.dumps([position.version, source, quote, failures, title, options], sort_keys=True, default=str)
    # 失败状态持续时无需每次增加一条相同分析。
    if not fresh:
        signature = json.dumps([position.version, source, timestamp, title, bad, options], sort_keys=True)
    return Analysis(title, text, conditions, quote_time, source_name, signature)


class RiskStore:
    """独立风险库：没有持仓表/持仓写入接口；schema事务失败不重建空库。"""
    def __init__(self, path):
        self.path = Path(path)
        existed = self.path.exists()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._db() as db:
                version = db.execute('PRAGMA user_version').fetchone()[0]
                identity = db.execute('PRAGMA application_id').fetchone()[0]
                if version == 0 and not existed:
                    self._create_schema(db)
                    db.execute(f'PRAGMA application_id={APP_ID}')
                    db.execute('PRAGMA user_version=1')
                elif version != 1 or identity != APP_ID:
                    raise RiskError('提醒库格式或版本不支持，未重置数据；持仓库未改动。')
                for table in ('risk_settings', 'risk_state', 'risk_records', 'risk_analysis'):
                    db.execute(f'SELECT * FROM {table} LIMIT 1')
        except OSError:
            raise RiskError('本地提醒目录不可写，请检查权限和磁盘空间；持仓未改动。') from None

    @staticmethod
    def _create_schema(db):
        db.execute('CREATE TABLE risk_settings (position_id INTEGER, code TEXT, revision INTEGER NOT NULL, '
                   'options TEXT NOT NULL, PRIMARY KEY(position_id,code))')
        db.execute('CREATE TABLE risk_state (position_id INTEGER, code TEXT, kind TEXT, revision INTEGER, '
                   'active INTEGER NOT NULL, emitted INTEGER NOT NULL, last_sent REAL, PRIMARY KEY(position_id,code,kind))')
        db.execute('CREATE TABLE risk_records (id INTEGER PRIMARY KEY, position_id INTEGER NOT NULL, code TEXT NOT NULL, '
                   'name TEXT NOT NULL, kind TEXT NOT NULL, triggered REAL NOT NULL, quote_time TEXT NOT NULL, '
                   'source TEXT NOT NULL, basis TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0)')
        db.execute('CREATE TABLE risk_analysis (id INTEGER PRIMARY KEY, position_id INTEGER NOT NULL, code TEXT NOT NULL, '
                   'created REAL NOT NULL, quote_time TEXT NOT NULL, source TEXT NOT NULL, title TEXT NOT NULL, '
                   'body TEXT NOT NULL, signature TEXT NOT NULL)')
        db.execute('CREATE INDEX records_stock ON risk_records(position_id,code,id)')
        db.execute('CREATE INDEX analysis_stock ON risk_analysis(position_id,code,id)')

    @contextmanager
    def _db(self):
        try:
            with closing(sqlite3.connect(self.path, timeout=1)) as db:
                db.row_factory = sqlite3.Row
                db.execute('BEGIN IMMEDIATE')
                with db:
                    yield db
        except sqlite3.Error:
            raise RiskError('本地提醒数据暂不可用，操作已回滚；持仓未改动。请检查占用、权限和磁盘空间。') from None

    @staticmethod
    def _settings(db, position_id, code):
        row = db.execute('SELECT * FROM risk_settings WHERE position_id=? AND code=?', (position_id, code)).fetchone()
        if not row:
            return settings(), 0
        try:
            values = json.loads(row['options'])
            if not isinstance(values,dict):
                raise RiskError('提醒设置格式无效。')
            return settings(values), row['revision']
        except (ValueError, TypeError):
            raise RiskError('提醒设置校验失败，未恢复默认值；请检查本地备份。') from None

    def load_settings(self, position_id, code):
        with self._db() as db:
            return self._settings(db, position_id, code)[0]

    def save_settings(self, position_id, code, values):
        values = settings(values)
        with self._db() as db:
            previous, revision = self._settings(db, position_id, code)
            if previous == values:
                return
            db.execute('INSERT INTO risk_settings VALUES (?,?,?,?) ON CONFLICT(position_id,code) DO UPDATE SET '
                       'revision=excluded.revision,options=excluded.options',
                       (position_id, code, revision + 1, json.dumps(values)))
            # 只重新待触发发生修改的条件；不能因改另一条件而重复通知。
            for kind in KINDS:
                keys = [kind+'_enabled'] + (['failures','stale_seconds'] if kind == 'data' else [kind])
                if any(previous[key] != values[key] for key in keys):
                    db.execute('UPDATE risk_state SET active=0,emitted=0 WHERE position_id=? AND code=? AND kind=?',
                               (position_id,code,kind))

    def process(self, position, quote, source, *, failures=0, now, force_record=False):
        """规则、记录、去重在一个事务里；返回新提醒，交给UI尝试系统通知。"""
        if position.archived:
            return None, []
        with self._db() as db:
            options, revision = self._settings(db, position.position_id, position.code)
            analysis = analyze(position, quote, source, options, failures=failures, now=now)
            key = (position.position_id, position.code)
            previous = db.execute('SELECT signature FROM risk_analysis WHERE position_id=? AND code=? '
                                  'ORDER BY id DESC LIMIT 1', key).fetchone()
            if force_record or previous is None or previous[0] != analysis.signature:
                db.execute('INSERT INTO risk_analysis(position_id,code,created,quote_time,source,title,body,signature) '
                           'VALUES (?,?,?,?,?,?,?,?)', (*key, now, analysis.quote_time, analysis.source,
                                                       analysis.title, analysis.text, analysis.signature))
            notices = []
            for kind, condition in analysis.conditions.items():
                enabled = options[kind + '_enabled']
                if condition is None and enabled:
                    continue  # 不足/失败/过期不能把价格条件误判为恢复。
                state = db.execute('SELECT * FROM risk_state WHERE position_id=? AND code=? AND kind=?', (*key,kind)).fetchone()
                active = bool(enabled and condition)
                emitted = bool(state and state['emitted'] and state['active']) if active else False
                last_sent = state['last_sent'] if state else None
                if active and not emitted and (last_sent is None or now - last_sent >= options['cooldown']):
                    threshold = ('连续失败至少%d次，或行情过期超过%d秒/时间异常' % (options['failures'],options['stale_seconds'])
                                 if kind == 'data' else KINDS[kind] + options[kind] + ('%' if kind in ('rise','fall') else '元'))
                    basis = threshold + '\n' + analysis.text
                    cursor = db.execute('INSERT INTO risk_records(position_id,code,name,kind,triggered,quote_time,source,basis) '
                                        'VALUES (?,?,?,?,?,?,?,?)', (*key,position.name,kind,now,analysis.quote_time,analysis.source,basis))
                    notices.append(dict(id=cursor.lastrowid, position_id=position.position_id, code=position.code,
                                        name=position.name, kind=kind, triggered=now, quote_time=analysis.quote_time,
                                        source=analysis.source, basis=basis))
                    emitted, last_sent = True, now
                db.execute('INSERT INTO risk_state VALUES (?,?,?,?,?,?,?) ON CONFLICT(position_id,code,kind) DO UPDATE SET '
                           'revision=excluded.revision,active=excluded.active,emitted=excluded.emitted,last_sent=excluded.last_sent',
                           (*key,kind,revision,int(active),int(emitted),last_sent))
            return analysis, notices

    def records(self, position_id=None, code=None, *, analyses=False, limit=200):
        with self._db() as db:
            table = 'risk_analysis' if analyses else 'risk_records'
            query = f'SELECT * FROM {table}'
            args = []
            if position_id is not None:
                query += ' WHERE position_id=? AND code=?'
                args.extend((position_id, code))
            query += ' ORDER BY id DESC LIMIT ?'
            args.append(min(max(int(limit),1),1000))
            return [dict(row) for row in db.execute(query,args)]

    def mark_read(self, record_id):
        with self._db() as db:
            db.execute('UPDATE risk_records SET is_read=1 WHERE id=?', (record_id,))
