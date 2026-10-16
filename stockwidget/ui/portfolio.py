"""中文单股票面板；只写本地持仓，复用原行情适配与桌面窗口。

新增于 2026-10-16，a-share-ai-butler，Apache-2.0。
"""
from datetime import datetime
from decimal import Decimal
import threading
import time

from PySide6.QtCore import QFile, QIODevice, QObject, QTimer, Signal
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QWidget, QVBoxLayout, QMessageBox

from stockwidget.core.portfolio import PortfolioError, money, valuation
from stockwidget.core.window_rules import quote_timestamp, QUOTE_TIMEZONE
from stockwidget.data.quotes import fetch_quote_result


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
        key = instrument["market"] + instrument["code"]

        def worker():
            result = fetch_quote_result({key: instrument}, source)
            try:
                self.finished.emit((key, source, generation, *result))
            except RuntimeError:
                pass  # 窗口已经退出，不访问已销毁的 Qt 对象。

        threading.Thread(target=worker, daemon=True).start()
        return True


class PortfolioPanel(QWidget):
    def __init__(self, store, app):
        super().__init__()
        self.store, self.app = store, app
        self.position = store.load()
        self.quote = None
        self.quote_source = None
        self.failed = False
        self.generation = 0
        self.source = app.win.data_source
        self.setWindowTitle("我的股票管家")
        self.resize(560, 670)
        self.setMinimumSize(440, 620)
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
        for name in ("pages", "stockLabel", "priceLabel", "changeLabel", "positionLabel", "profitLabel",
                     "valueLabel", "lockLabel", "sourceLabel", "timeLabel", "statusLabel", "nameEdit",
                     "codeEdit", "totalEdit", "costEdit", "editLockLabel", "lockedEdit", "sourceCombo", "intervalSpin"):
            setattr(self, name, self.form.findChild(QObject, name))
        actions = {
            "editButton": self.edit_position, "detailsButton": self.show_details,
            "settingsButton": self.open_settings, "refreshButton": self.refresh,
            "savePositionButton": self.save_position, "cancelEditButton": self.home,
            "saveLockedButton": self.save_locked, "saveQuoteSettingsButton": self.save_quote_settings,
            "backButton": self.home, "floatSettingsButton": app.open_settings,
        }
        for name, callback in actions.items():
            self.form.findChild(QObject, name).clicked.connect(callback)
        self.form.findChild(QObject, "editButton").setDefault(True)
        self.poller = QuotePoller(self)
        self.poller.finished.connect(self.accept_quote)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.render)
        self.clock.start(5000)  # 即使没有新响应，也更新“过期”标记。
        app.win.configuration_changed.connect(self.sync_quote_settings)
        self.sync_quote_settings()
        self.render()
        QTimer.singleShot(0, self.refresh)

    def home(self):
        self.pages.setCurrentIndex(0)
        self.render()

    def show_panel(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def render(self):
        p = self.position
        self.stockLabel.setText(f"{p.name}  {p.code}")
        self.positionLabel.setText(f"我的持仓：{p.total} 股    成本价：{p.cost} 元/股")
        self.lockLabel.setText(f"锁定仓（受保护）：{p.locked} 股    策略仓：{p.strategy} 股")
        self.sourceLabel.setText("行情来源：" + {"sina": "新浪财经", "eastmoney": "东方财富"}[self.source])
        result = valuation(p, self.quote.get("current_price") if self.quote else None)
        if result is None:
            self.priceLabel.setText("暂无行情")
            self.changeLabel.setText("今日涨跌：暂无行情")
            self.profitLabel.setText("持仓盈亏：暂无行情")
            self.valueLabel.setText("估算市值：暂无行情")
            self.timeLabel.setText("行情时间：未知（无法判断新鲜度）")
            self.priceLabel.setStyleSheet("")
            self.changeLabel.setStyleSheet("")
            self.profitLabel.setStyleSheet("")
        else:
            market_value, profit, percentage = result
            self.priceLabel.setText(f"{money(self.quote['current_price']):.2f} 元")
            try:
                previous = money(self.quote.get("prev_close"))
            except PortfolioError:
                previous = Decimal(0)
            change = money(self.quote["current_price"]) - previous if previous else None
            self.changeLabel.setText(f"今日涨跌：{change:+.2f} 元（{change / previous * 100:+.2f}%）"
                                     if change is not None else "今日涨跌：暂无有效昨收价")
            self._color(self.priceLabel, change)
            self._color(self.changeLabel, change)
            pct = f"{percentage:+.2f}%" if percentage is not None else "成本为0，比例不适用"
            self.profitLabel.setText(f"持仓盈亏：{profit:+.2f} 元（{pct}）")
            self._color(self.profitLabel, profit)
            self.valueLabel.setText(f"估算市值：{market_value:.2f} 元")
            timestamp = quote_timestamp(self.quote)
            if timestamp is None:
                self.timeLabel.setText("行情时间：未知，无法判断是否过期")
            else:
                age = time.time() - timestamp
                status = "已过期 / 非实时" if age > 120 else "来源最近可获取数据，仍可能延迟"
                if age < -60:
                    status = "时间异常，无法判断新鲜度"
                raw_time = datetime.fromtimestamp(timestamp, QUOTE_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
                self.timeLabel.setText(f"行情时间：{raw_time}（北京时间）\n{status}")
        interval = self.timer.interval() // 1000
        self.statusLabel.setText(("行情更新失败；显示上次行情。" if self.failed and result else
                                  "行情更新失败；暂无行情。" if self.failed else "")
                                 + f"每{interval}秒尝试刷新，不保证实时；休市时为最近交易数据。")

    @staticmethod
    def _color(label, value):
        color = "#ff7878" if value is not None and value > 0 else "#61ce9c" if value is not None and value < 0 else ""
        label.setStyleSheet(f"color: {color};" if color else "")

    def edit_position(self):
        try:
            self.position = self.store.load()
        except PortfolioError as error:
            self._error(error)
            return
        p = self.position
        self.edit_version = p.version
        for widget, value in ((self.nameEdit, p.name), (self.codeEdit, p.code), (self.totalEdit, p.total), (self.costEdit, p.cost)):
            widget.setText(str(value))
        self.editLockLabel.setText(f"锁定仓：{p.locked} 股（此页不可修改）")
        self.pages.setCurrentIndex(1)

    def save_position(self):
        try:
            previous_code = self.position.code
            self.position = self.store.update_position(name=self.nameEdit.text(), code=self.codeEdit.text(),
                total=self.totalEdit.text(), cost=self.costEdit.text(), expected_version=self.edit_version)
        except PortfolioError as error:
            self._error(error)
            return
        if previous_code != self.position.code:
            self.generation += 1
            self.quote = None
            self.failed = False
        self.home()
        self.refresh()

    def open_settings(self):
        try:
            self.position = self.store.load()
        except PortfolioError as error:
            self._error(error)
            return
        self.lockedEdit.setText(str(self.position.locked))
        self.sourceCombo.setCurrentIndex(1 if self.source == "eastmoney" else 0)
        self.intervalSpin.setValue(self.timer.interval() // 1000)
        self.pages.setCurrentIndex(2)

    def save_locked(self):
        try:
            ticket = self.store.begin_lock_change(self.lockedEdit.text())
            answer = QMessageBox.question(self, "再次确认锁定仓修改",
                f"确定将锁定仓修改为 {self.lockedEdit.text()} 股？\n仅更新本地记录，不执行任何交易。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            self.position = self.store.confirm_lock_change(ticket, confirmed=answer == QMessageBox.Yes)
        except PortfolioError as error:
            self._error(error)
            return
        self.home()

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
            self.quote = None
            self.failed = False
        interval = max(30, min(3600, self.app.win.refresh_seconds))
        self.timer.start(interval * 1000)
        self.render()

    def refresh(self):
        self.poller.fetch(self.position.instrument(), self.source, self.generation)

    def accept_quote(self, payload):
        self.poller.busy = False
        key, source, generation, ok, data, _error = payload
        instrument = self.position.instrument()
        if generation != self.generation or key != instrument["market"] + instrument["code"] or source != self.source:
            QTimer.singleShot(0, self.refresh)
            return
        quote = (data or {}).get(key) if ok else None
        if quote and valuation(self.position, quote.get("current_price")) is not None:
            self.quote, self.quote_source, self.failed = dict(quote), source, False
        else:
            self.failed = True
        self.render()

    def show_details(self):
        p = self.position
        entry = p.instrument()
        watchlist = dict(self.app.win.watchlist)
        watchlist[entry["market"] + entry["code"]] = entry
        self.app.win.set_watchlist(watchlist)
        if not self.app.win.widget_visible:
            self.app.win.toggle_win()
        self.app.win.raise_()

    def _error(self, error):
        QMessageBox.warning(self, "未保存", str(error))

    def closeEvent(self, event):
        self.hide()  # 通过托盘“我的持仓”恢复，定时行情仍继续。
        event.ignore()
