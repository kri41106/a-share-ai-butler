"""规则分析、提醒配置与本地历史；复用持仓请求和原系统托盘。
Added 2026-10-17 for a-share-ai-butler; Apache-2.0.
"""
import time
from PySide6.QtCore import QFile, QIODevice, QObject, QTimer, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QTableWidgetItem, QSystemTrayIcon
from PySide6.QtUiTools import QUiLoader

from stockwidget.core.risk import RiskStore, RiskError, KINDS, clock_text
from stockwidget.core.portfolio import PortfolioError
from stockwidget.ui.controls.style import apply_portfolio_theme


class RiskController(QObject):
    """窗口线程读取快照并写独立风险库；不修改任何持仓。"""
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.store = None
        self.error = ''
        self.window = None
        self.failures = {}
        self.results = {}
        self._init_store()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.evaluate)
        self.timer.start(30000)  # 只检查时间过期，不发起任何网络请求。

    def _init_store(self):
        try:
            self.store = RiskStore(self.panel.store.path.parent / 'risk.sqlite3')
            self.error = ''
        except RiskError as error:
            self.store, self.error = None, str(error)

    def reset_source(self):
        self.failures.clear()
        self.results.clear()

    def quote_received(self):
        current = {(p.position_id,p.code) for p in self.panel.positions}
        self.failures = {key:value for key,value in self.failures.items() if key in current}
        for p in self.panel.positions:
            key = (p.position_id,p.code)
            self.failures[key] = self.failures.get(key,0) + 1 if p.code in self.panel.failed_codes else 0
        self.evaluate()

    def evaluate(self, *, force_id=None):
        if self.store is None:
            return
        try:
            # 读取最新快照，归档股票不再监测；成本等数据绝不进入行情请求。
            positions = self.panel.store.load_all()
            now = time.time()
            for p in positions:
                result, notices = self.store.process(p, self.panel.quotes.get(p.code), self.panel.source,
                    failures=self.failures.get((p.position_id,p.code),0), now=now,
                    force_record=p.position_id == force_id)
                self.results[(p.position_id,p.code)] = result
                for notice in notices:
                    self.notify(notice, self.panel.quotes.get(p.code))
            self.error = ''
        except (RiskError,PortfolioError) as error:
            self.error = str(error)  # 不记录个人持仓或错误栈。
        if self.window is not None and self.window.isVisible():
            self.window.show_analysis()

    def notify(self, notice, quote):
        tray = getattr(self.panel.app,'tray',None)
        if tray is None:
            return
        try:
            if QSystemTrayIcon.supportsMessages() and tray.isVisible():
                price = (quote or {}).get('current_price')
                text = (notice['basis'].split('\n',1)[0] + '\n最新可得价格：' + (str(price) + '元' if price else '暂无行情')
                        + '\n' + notice['source'] + '；' + notice['quote_time'] + '\n仅供参考；不会执行交易。')
                tray.showMessage(notice['name'] + ' ' + notice['code'],text,
                                 QSystemTrayIcon.Information,10000)
        except (RuntimeError,TypeError):
            pass  # 系统通知失败不丢失已提交的记录，不回写持仓。

    def open(self, index=0):
        if self.store is None:
            self._init_store()  # 用户再次打开时才重试，不无限自动重试。
        if self.window is None:
            self.window = RiskCenter(self)
        self.evaluate()
        self.window.bind_position(self.panel.position)
        self.window.tabs.setCurrentIndex(index)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        return self.window


class RiskCenter(QWidget):
    def __init__(self, controller):
        super().__init__(controller.panel,Qt.Window)
        self.controller = controller
        self.position = None
        self.setWindowTitle('风险分析与提醒 · 本地规则')
        self.resize(900,690)
        self.setMinimumSize(650,500)
        apply_portfolio_theme(self)
        file = QFile(':/risk.ui')
        if not file.open(QIODevice.ReadOnly):
            raise RiskError('无法读取提醒中心界面资源。')
        try:
            self.form = QUiLoader().load(file,self)
        finally:
            file.close()
        if self.form is None:
            raise RiskError('无法加载提醒中心界面。')
        QVBoxLayout(self).addWidget(self.form)
        names = ('tabs','stockLabel','errorLabel','analysisText','analyzeButton','recordMode','selectedOnly',
                 'recordsTable','recordDetails','markReadButton','saveAlertsButton')
        for name in names:
            setattr(self,name,self.form.findChild(QObject,name))
        for kind in KINDS:
            setattr(self,kind+'Enabled',self.form.findChild(QObject,kind+'Enabled'))
            if kind != 'data':
                setattr(self,kind+'Edit',self.form.findChild(QObject,kind+'Edit'))
        for name in ('failures','stale_seconds','cooldown'):
            setattr(self,name+'Spin',self.form.findChild(QObject,name+'Spin'))
        self.cooldownSpin.valueChanged.connect(lambda value: self.cooldownSpin.setSuffix(f' 秒（{value / 60:g} 分钟）'))
        self.cooldownSpin.setSuffix(f' 秒（{self.cooldownSpin.value() / 60:g} 分钟）')
        self.saveAlertsButton.clicked.connect(self.save)
        self.analyzeButton.clicked.connect(self.run_analysis)
        self.tabs.currentChanged.connect(self.refresh_records)
        self.recordMode.currentIndexChanged.connect(self.refresh_records)
        self.selectedOnly.toggled.connect(self.refresh_records)
        self.form.findChild(QObject,'refreshRecordsButton').clicked.connect(self.refresh_records)
        self.recordsTable.currentCellChanged.connect(self.select_record)
        self.markReadButton.clicked.connect(self.mark_read)
        self.rows = []

    def bind_position(self,position):
        self.position = position
        self.stockLabel.setText(f'{position.name} {position.code}' if position else '未选中股票，可查看全部历史')
        for widget in (self.saveAlertsButton,self.analyzeButton):
            widget.setEnabled(position is not None and self.controller.store is not None)
        self.selectedOnly.setChecked(position is not None)
        if position and self.controller.store:
            try:
                options = self.controller.store.load_settings(position.position_id,position.code)
                for kind in KINDS:
                    getattr(self,kind+'Enabled').setChecked(options[kind+'_enabled'])
                    if kind != 'data':
                        getattr(self,kind+'Edit').setText(options[kind] or '')
                for name in ('failures','stale_seconds','cooldown'):
                    getattr(self,name+'Spin').setValue(options[name])
            except RiskError as error:
                self.errorLabel.setText(str(error))
                self.saveAlertsButton.setEnabled(False)
                return
        self.show_analysis()
        self.refresh_records()

    def valid_position(self):
        if self.position is None:
            raise RiskError('请先在首页选中一只股票。')
        current = self.controller.panel.store.load(self.position.position_id)
        if current.code != self.position.code:
            raise RiskError('股票代码已更改，请关闭本窗口后重新打开。')
        return current

    def save(self):
        try:
            p = self.valid_position()
            if self.controller.store is None:
                raise RiskError('提醒库不可用，设置未保存。')
            values = {}
            for kind in KINDS:
                values[kind+'_enabled'] = getattr(self,kind+'Enabled').isChecked()
                if kind != 'data':
                    values[kind] = getattr(self,kind+'Edit').text().strip()
            for name in ('failures','stale_seconds','cooldown'):
                values[name] = getattr(self,name+'Spin').value()
            self.controller.store.save_settings(p.position_id,p.code,values)
            self.controller.evaluate()
            self.errorLabel.setText('已保存本机设置；已满足条件会立即记录，系统允许时通知。')
        except (RiskError,PortfolioError) as error:
            self.errorLabel.setText(str(error))

    def run_analysis(self):
        try:
            p = self.valid_position()
            self.controller.evaluate(force_id=p.position_id)
            self.show_analysis()
        except (RiskError,PortfolioError) as error:
            self.errorLabel.setText(str(error))

    def show_analysis(self):
        self.errorLabel.setText(self.controller.error)
        result = self.controller.results.get((self.position.position_id,self.position.code)) if self.position else None
        self.analysisText.setPlainText(result.text if result else '数据不足，暂无分析。请选中股票，检查行情与本地数据状态。')

    def refresh_records(self,*_args):
        self.rows = []
        self.recordDetails.clear()
        self.markReadButton.setEnabled(False)
        if self.controller.store:
            try:
                p = self.position if self.selectedOnly.isChecked() else None
                if self.selectedOnly.isChecked() and p is None:
                    return
                self.rows = self.controller.store.records(p.position_id if p else None,p.code if p else None,
                                                         analyses=self.recordMode.currentIndex() == 1)
            except RiskError as error:
                self.errorLabel.setText(str(error))
        analyses = self.recordMode.currentIndex() == 1
        table = self.recordsTable
        table.blockSignals(True)
        table.clear()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(['股票代码','分析时间' if analyses else '触发时间','结论 / 提醒条件','行情时间 / 来源','状态'])
        table.setRowCount(len(self.rows))
        for index,row in enumerate(self.rows):
            texts = (row['code'],clock_text(row['created'] if analyses else row['triggered']),
                     row['title'] if analyses else KINDS[row['kind']],row['quote_time'] + '\n' + row['source'],
                     '分析记录' if analyses else '已读' if row['is_read'] else '未读')
            for column,text in enumerate(texts):
                table.setItem(index,column,QTableWidgetItem(text))
            table.setRowHeight(index,56)
        for column,width in enumerate((125,175,235,235,75)):
            table.setColumnWidth(column,width)
        table.blockSignals(False)
        if self.rows:
            table.setCurrentCell(0,0)
            self.select_record(0)

    def select_record(self,row,*_args):
        valid = 0 <= row < len(self.rows)
        analyses = self.recordMode.currentIndex() == 1
        self.recordDetails.setPlainText((self.rows[row]['body'] if analyses else self.rows[row]['basis']) if valid else '')
        self.markReadButton.setEnabled(valid and not analyses)

    def mark_read(self):
        row = self.recordsTable.currentRow()
        if not 0 <= row < len(self.rows) or self.recordMode.currentIndex() != 0:
            return
        try:
            self.controller.store.mark_read(self.rows[row]['id'])
            self.refresh_records()
        except RiskError as error:
            self.errorLabel.setText(str(error))
