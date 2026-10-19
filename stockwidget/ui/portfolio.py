"""中文多股票面板；只写本地持仓，复用原行情适配与桌面窗口。

新增于 2026-10-16，a-share-ai-butler，Apache-2.0。
"""
from datetime import datetime
from decimal import Decimal
import threading
import time

from PySide6.QtCore import QFile, QIODevice, QObject, QTimer, Signal, Qt
from PySide6.QtGui import QColor
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QWidget, QVBoxLayout, QMessageBox, QTableWidgetItem, QHeaderView

from stockwidget.core.portfolio import PortfolioError, money, valuation, portfolio_totals
from stockwidget.ui.controls.style import apply_portfolio_theme
from stockwidget.core.window_rules import quote_timestamp, QUOTE_TIMEZONE
from stockwidget.data.quotes import fetch_quote_result
from stockwidget.ui.risk import RiskController
from stockwidget.ui.briefing import BriefingCenter
from stockwidget.data.research import ResearchError


class QuotePoller(QObject):
    """与持仓数据库隔离的只读后台请求；不传递成本或持仓股数。"""
    finished = Signal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self.busy = False

    def fetch(self, instrument, source, generation):
        if self.busy:
            return False
        self.busy = True
        instruments = ({instrument["market"] + instrument["code"]: instrument}
                       if "code" in instrument else instrument)
        keys = tuple(instruments)

        def worker():
            result = fetch_quote_result(instruments, source)
            try:
                self.finished.emit((keys, source, generation, *result))
            except RuntimeError:
                pass  # 窗口已经退出，不访问已销毁的 Qt 对象。

        threading.Thread(target=worker, daemon=True).start()
        return True


class PortfolioPanel(QWidget):
    def __init__(self, store, app):
        super().__init__()
        self.store, self.app = store, app
        self.positions = store.load_all()
        self.position = self.positions[0] if self.positions else None
        self.quotes, self.failed_codes = {}, set()
        self.generation = 0
        self.source = app.win.data_source
        self.setWindowTitle("我的股票管家 · 多股票版")
        self.resize(1000, 720)
        self.setMinimumSize(680, 540)
        apply_portfolio_theme(self)
        form = QFile(":/portfolio.ui")
        if not form.open(QIODevice.ReadOnly):
            raise PortfolioError("无法读取持仓界面资源。")
        try:
            self.form = QUiLoader().load(form, self)
        finally:
            form.close()
        if self.form is None:
            raise PortfolioError("无法加载持仓界面。")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.form)
        for name in ("pages", "positionsTable", "summaryValueLabel", "summaryProfitLabel", "coverageLabel",
                     "sourceLabel", "statusLabel", "nameEdit", "codeEdit", "totalEdit", "costEdit",
                     "editLockLabel", "lockedEdit", "sourceCombo", "intervalSpin", "restoreCombo"):
            setattr(self, name, self.form.findChild(QObject, name))
        self.nameEdit.setPlaceholderText("例如：博纳影业")
        self.codeEdit.setPlaceholderText("例如：001330.SZ")
        self.totalEdit.setPlaceholderText("非负整数")
        self.costEdit.setPlaceholderText("每股成本，最多六位小数")
        table = self.positionsTable
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(["股票 / 代码", "价格 / 今日涨跌", "持仓 / 成本", "浮动盈亏 / 比例",
                                         "锁定仓 / 策略仓", "行情时间 / 数据状态"])
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for column, width in enumerate((145, 155, 135, 165, 140, 240)):
            table.setColumnWidth(column, width)
        table.currentCellChanged.connect(self.select_position)
        actions = {"addButton": self.add_position, "editButton": self.edit_position,
                   "archiveButton": self.archive_position, "detailsButton": self.show_details,
                   "settingsButton": self.open_settings, "refreshButton": self.refresh,
                   "savePositionButton": self.save_position, "cancelEditButton": self.home,
                   "saveLockedButton": self.save_locked, "saveQuoteSettingsButton": self.save_quote_settings,
                   "restoreButton": self.restore_position, "backButton": self.home,
                   "floatSettingsButton": app.open_settings}
        for name, callback in actions.items():
            self.form.findChild(QObject, name).clicked.connect(callback)
        self.poller = QuotePoller(self)
        self.poller.finished.connect(self.accept_quote)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.render)
        self.clock.start(5000)
        self.briefing = None
        for name,index in (("newsButton",0),("summaryButton",1),("chatgptButton",2),("importAiButton",3),("aiHistoryButton",3)):
            self.form.findChild(QObject,name).clicked.connect(lambda _checked=False,tab=index:self.open_briefing(tab))
        self.risk = RiskController(self)
        for name, index in (("riskButton",0),("alertsButton",1),("recordsButton",2)):
            self.form.findChild(QObject,name).clicked.connect(lambda _checked=False, tab=index: self.risk.open(tab))
        app.win.configuration_changed.connect(self.sync_quote_settings)
        self.sync_quote_settings()
        QTimer.singleShot(0, self.refresh)

    def open_briefing(self,index=0,*,auto_fetch=True):
        try:
            if self.briefing is None:self.briefing=BriefingCenter(self)
            return self.briefing.open_page(index,auto_fetch=auto_fetch)
        except (ResearchError,PortfolioError) as error:self._error(error)

    def home(self):
        self.pages.setCurrentIndex(0)
        self.render()

    def show_panel(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    @staticmethod
    def _key(position):
        instrument = position.instrument()
        return instrument["market"] + instrument["code"]

    def _reload(self, preferred=None):
        previous = {p.code for p in self.positions}
        positions = self.store.load_all()
        current = {p.code for p in positions}
        if current != previous:
            self.generation += 1
            self.quotes = {code: quote for code, quote in self.quotes.items() if code in current}
            self.failed_codes.intersection_update(current)
        preferred = preferred or (self.position.position_id if self.position else None)
        self.positions = positions
        self.position = next((p for p in positions if p.position_id == preferred), positions[0] if positions else None)

    def select_position(self, row, *_args):
        self.position = self.positions[row] if 0 <= row < len(self.positions) else None

    @staticmethod
    def quote_state(quote):
        if not quote:
            return "暂无行情", False
        timestamp = quote_timestamp(quote)
        if timestamp is None:
            return "行情时间未知，无法判断是否过期", True
        age = time.time() - timestamp
        state = "已过期 / 非实时" if age > 120 else "最近可得，仍可能延迟"
        if age < -60:
            state = "时间异常，无法判断新鲜度"
        raw = datetime.fromtimestamp(timestamp, QUOTE_TIMEZONE).strftime("%m-%d %H:%M:%S")
        return f"{raw}（北京时间）\n{state}", age > 120 or age < -60

    @staticmethod
    def _color(value):
        return "#ff7878" if value is not None and value > 0 else "#61ce9c" if value is not None and value < 0 else "#eeeeee"

    def render(self):
        totals = portfolio_totals(self.positions, self.quotes)
        covered, count = totals["covered"], totals["total"]
        prefix = "全部持仓" if covered == count else "已覆盖部分"
        self.summaryValueLabel.setText(f"{prefix}总市值：{totals['value']:.2f} 元（估算）"
                                       if covered else "总市值：暂无行情" if count else "暂无持仓，请添加股票")
        self.summaryProfitLabel.setText(f"{prefix}总浮动盈亏：{totals['profit']:+.2f} 元"
                                        if covered else "总浮动盈亏：暂无行情")
        self.summaryProfitLabel.setStyleSheet(f"color: {self._color(totals['profit'])};")
        stale = sum(self.quote_state(self.quotes.get(p.code))[1] for p in self.positions)
        incomplete = "；数据不完整" if covered < count else ""
        self.coverageLabel.setText(f"持仓股票：{count} 只    行情覆盖：{covered}/{count}{incomplete}    过期或时间未知：{stale} 只")
        selected = self.position.position_id if self.position else None
        table = self.positionsTable
        table.blockSignals(True)
        table.setRowCount(count)
        for row, p in enumerate(self.positions):
            quote = self.quotes.get(p.code)
            result = valuation(p, quote.get("current_price") if quote else None)
            price, profit_text, change, profit = "暂无行情", "暂无行情", None, None
            if result:
                price = f"{money(quote['current_price']):.2f} 元"
                try:
                    previous = money(quote.get("prev_close"))
                except PortfolioError:
                    previous = Decimal(0)
                if previous:
                    change = money(quote["current_price"]) - previous
                    price += f"\n{change / previous * 100:+.2f}%"
                else:
                    price += "\n暂无有效昨收价"
                profit, pct = result[1], result[2]
                percentage = f"{pct:+.2f}%" if pct is not None else "成本为0，比例不适用"
                profit_text = f"{profit:+.2f} 元\n{percentage}"
            state = self.quote_state(quote)[0]
            if p.code in self.failed_codes:
                state += "\n更新失败，显示上次行情" if quote else "\n行情更新失败"
            texts = (f"{p.name}\n{p.code}", price, f"{p.total} 股\n成本 {p.cost} 元", profit_text,
                     f"锁定 {p.locked} 股\n策略 {p.strategy} 股", state)
            for column, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, p.position_id)
                item.setForeground(QColor(self._color(change if column == 1 else profit if column == 3 else None)))
                table.setItem(row, column, item)
            table.setRowHeight(row, 82)
            if p.position_id == selected:
                table.setCurrentCell(row, 0)
        table.blockSignals(False)
        for name in ("editButton", "detailsButton", "archiveButton"):
            self.form.findChild(QObject, name).setEnabled(self.position is not None)
        self.sourceLabel.setText("行情来源：" + {"sina": "新浪财经", "eastmoney": "东方财富"}[self.source])
        self.statusLabel.setText(f"每{self.timer.interval() // 1000}秒批量尝试刷新；不保证实时，休市显示最近交易数据。")

    def _edit_form(self, position):
        self.edit_id = position.position_id if position else None
        self.edit_version = position.version if position else None
        for widget, value in ((self.nameEdit, position.name if position else ""),
                              (self.codeEdit, position.code if position else ""),
                              (self.totalEdit, position.total if position else ""),
                              (self.costEdit, position.cost if position else "")):
            widget.setText(str(value))
        self.form.findChild(QObject, "editTitle").setText("修改本地持仓" if position else "添加 A 股持仓")
        self.editLockLabel.setText(f"锁定仓：{position.locked if position else 0} 股（此页不可修改）")
        self.pages.setCurrentIndex(1)

    def add_position(self):
        self._edit_form(None)

    def edit_position(self):
        if self.position is None:
            return
        try:
            self._edit_form(self.store.load(self.position.position_id))
        except PortfolioError as error:
            self._error(error)

    def save_position(self):
        try:
            # Validate before confirmation; rejected inputs never alter the database.
            from stockwidget.core.portfolio import Position, a_share_code, shares, validate_position
            candidate = Position(self.nameEdit.text().strip(), a_share_code(self.codeEdit.text()),
                                 shares(self.totalEdit.text()), money(self.costEdit.text()),
                                 self.store.load(self.edit_id).locked if self.edit_id is not None else 0)
            validate_position(candidate)
            if not self._question("确认保存本地持仓", f"保存 {candidate.name} {candidate.code} 的本地持仓？\n"
                                  f"总股数 {candidate.total}，每股成本 {candidate.cost} 元。\n不会执行任何真实交易。"):
                return
            values = dict(name=candidate.name, code=candidate.code, total=candidate.total, cost=candidate.cost)
            if self.edit_id is None:
                position = self.store.add_position(**values)
            else:
                position = self.store.update_position(**values, position_id=self.edit_id, expected_version=self.edit_version)
            self._reload(position.position_id)
            self.home()
            self.refresh()
        except PortfolioError as error:
            self._error(error)

    def open_settings(self):
        try:
            self._reload()
            self.lock_position = self.position
            self.lockedEdit.setText(str(self.position.locked) if self.position else "")
            self.lockedEdit.setEnabled(self.position is not None)
            self.form.findChild(QObject, "saveLockedButton").setEnabled(self.position is not None)
            self.form.findChild(QObject, "settingsStockLabel").setText(
                f"锁定仓设置：{self.position.name} {self.position.code}" if self.position else "尚无选中股票")
            self.sourceCombo.setCurrentIndex(1 if self.source == "eastmoney" else 0)
            self.intervalSpin.setValue(self.timer.interval() // 1000)
            self.restoreCombo.clear()
            for position in self.store.load_all(include_archived=True):
                if position.archived:
                    self.restoreCombo.addItem(f"{position.name} {position.code}", position)
            self.form.findChild(QObject, "restoreButton").setEnabled(self.restoreCombo.count() > 0)
            self.pages.setCurrentIndex(2)
        except PortfolioError as error:
            self._error(error)

    def save_locked(self):
        if self.lock_position is None:
            return
        try:
            p = self.lock_position
            ticket = self.store.begin_lock_change(self.lockedEdit.text(), position_id=p.position_id,
                                                   expected_version=p.version)
            answer = self._question("再次确认锁定仓修改", f"确定将 {p.name} {p.code} 的锁定仓改为 "
                                    f"{self.lockedEdit.text()} 股？\n仅更新本地记录。")
            self.store.confirm_lock_change(ticket, confirmed=answer)
            self._reload(p.position_id)
            self.home()
        except PortfolioError as error:
            self._error(error)

    def archive_position(self):
        if self.position is None:
            return
        try:
            p = self.position
            if not self._question("再次确认归档", f"归档 {p.name} {p.code}？\n该持仓仍有 {p.total} 股本地记录。"
                                  "\n不会卖出股票，历史与锁定仓保留，可在设置中恢复。"):
                return
            self.store.set_archived(p.position_id, expected_version=p.version, confirmed=True)
            self._reload()
            self.home()
        except PortfolioError as error:
            self._error(error)

    def restore_position(self):
        p = self.restoreCombo.currentData()
        if p is None or not self._question("确认恢复归档", f"恢复 {p.name} {p.code} 及原有数量、成本和锁定仓？"):
            return
        try:
            self.store.set_archived(p.position_id, expected_version=p.version, archived=False, confirmed=True)
            self._reload(p.position_id)
            self.home()
            self.refresh()
        except PortfolioError as error:
            self._error(error)

    def save_quote_settings(self):
        self.app.win.set_refresh_interval(self.intervalSpin.value())
        self.app.win.set_data_source("eastmoney" if self.sourceCombo.currentIndex() else "sina")
        self.home()
        self.refresh()

    def sync_quote_settings(self):
        source = self.app.win.data_source
        if self.source != source:
            self.generation += 1
            self.source = source
            self.quotes.clear()
            self.failed_codes.clear()
            self.risk.reset_source()
        interval = max(30, min(3600, self.app.win.refresh_seconds))
        self.timer.start(interval * 1000)
        self.render()

    def refresh(self):
        if self.positions:
            self.poller.fetch({self._key(p): p.instrument() for p in self.positions}, self.source, self.generation)

    def accept_quote(self, payload):
        self.poller.busy = False
        keys, source, generation, ok, data, _error = payload
        if generation != self.generation or source != self.source:
            QTimer.singleShot(0, self.refresh)
            return
        for position in self.positions:
            quote = (data or {}).get(self._key(position)) if ok else None
            if quote and valuation(position, quote.get("current_price")) is not None:
                self.quotes[position.code] = dict(quote)
                self.failed_codes.discard(position.code)
            else:
                self.failed_codes.add(position.code)
        self.risk.quote_received()
        self.render()

    def show_details(self):
        if self.position is None:
            return
        entry = self.position.instrument()
        watchlist = dict(self.app.win.watchlist)
        watchlist[entry["market"] + entry["code"]] = entry
        self.app.win.set_watchlist(watchlist)
        if not self.app.win.widget_visible:
            self.app.win.toggle_win()
        self.app.win.raise_()

    def _question(self, title, text):
        box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.Yes | QMessageBox.No, self)
        box.setDefaultButton(QMessageBox.No)
        apply_portfolio_theme(box)
        return box.exec() == QMessageBox.Yes

    def _error(self, error):
        box = QMessageBox(QMessageBox.Warning, "未保存", str(error), QMessageBox.Ok, self)
        apply_portfolio_theme(box)
        box.exec()

    def closeEvent(self, event):
        self.hide()
        event.ignore()
