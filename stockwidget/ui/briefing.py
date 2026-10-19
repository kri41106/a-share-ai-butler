"""公告资料、规则汇总与手动ChatGPT工作流；任何外部文本均为纯文本。
Added 2026-10-19, a-share-ai-butler; Apache-2.0.
"""
import json
import threading
import time
from PySide6.QtCore import QFile,QIODevice,QObject,QTimer,Signal,Qt,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget,QVBoxLayout,QTableWidgetItem,QApplication
from PySide6.QtUiTools import QUiLoader
from stockwidget.core.portfolio import PortfolioError
from stockwidget.core.risk import clock_text,RiskError
from stockwidget.core.briefing import BriefingStore,build_packet,source_status,report_text
from stockwidget.data.research import fetch_announcements,fetch_news,ResearchError,safe_link,TTL
from stockwidget.ui.controls.style import apply_portfolio_theme


class BriefingCenter(QWidget):
    fetched=Signal(object)
    progress=Signal(str)

    def __init__(self,panel):
        super().__init__(panel,Qt.Window)
        self.panel=panel
        self.store=BriefingStore(panel.store.path.parent/'research.sqlite3')
        self.positions=[];self.draft=None;self.article_rows=[];self.report_rows=[];self.busy=False
        self.setWindowTitle('新闻公告与综合分析 · 手动ChatGPT资料')
        self.resize(1050,700);self.setMinimumSize(750,580)
        apply_portfolio_theme(self)
        file=QFile(':/briefing.ui')
        if not file.open(QIODevice.ReadOnly):raise ResearchError('无法读取综合分析界面资源。')
        try:self.form=QUiLoader().load(file,self)
        finally:file.close()
        if self.form is None:raise ResearchError('无法加载综合分析界面。')
        QVBoxLayout(self).addWidget(self.form)
        names=('scopeCombo','statusLabel','tabs','articleFilter','articlesTable','articleDetails','overviewText',
               'privateCheck','promptText','packetCombo','reportTitleEdit','importText','reportsTable','reportDetails')
        for name in names:setattr(self,name,self.form.findChild(QObject,name))
        self.statusLabel.setMaximumHeight(85)
        self.scopeCombo.currentIndexChanged.connect(self.rebuild)
        self.privateCheck.toggled.connect(self.rebuild)
        self.articleFilter.currentIndexChanged.connect(self.render_articles)
        self.articlesTable.currentCellChanged.connect(self.select_article)
        self.reportsTable.currentCellChanged.connect(self.select_report)
        callbacks={'reloadPositionsButton':self.reload_positions,'fetchButton':lambda:self.fetch(True),
                   'openArticleButton':self.open_article,'buildOverviewButton':self.rebuild,'buildPromptButton':self.rebuild,
                   'copyPromptButton':self.copy_prompt,'openChatGPTButton':self.open_chatgpt,
                   'importReportButton':self.import_report,'refreshReportsButton':self.refresh_reports,
                   'copyReportButton':self.copy_report,'deleteReportButton':self.delete_report}
        for name,callback in callbacks.items():self.form.findChild(QObject,name).clicked.connect(callback)
        self.fetched.connect(self.on_fetched)
        self.progress.connect(self.statusLabel.setText)

    def open_page(self,index,*,auto_fetch=True):
        self.privateCheck.setChecked(False)
        self.reload_positions(prefer_selected=True)
        self.tabs.setCurrentIndex(index)
        self.show();self.raise_();self.activateWindow()
        if index==0 and auto_fetch:QTimer.singleShot(0,lambda:self.fetch(False))
        return self

    def reload_positions(self,prefer_selected=False):
        try:
            previous=self.scopeCombo.currentData()
            self.positions=self.panel.store.load_all()
            selected=self.panel.position
            self.scopeCombo.blockSignals(True);self.scopeCombo.clear()
            self.scopeCombo.addItem('全部持仓','')
            for p in self.positions:self.scopeCombo.addItem(p.name+' '+p.code,f'{p.position_id}|{p.code}')
            selected_key=f'{selected.position_id}|{selected.code}' if selected else ''
            preferred=selected_key if prefer_selected or previous is None else previous
            index=self.scopeCombo.findData(preferred)
            self.scopeCombo.setCurrentIndex(index if index>=0 else 0);self.scopeCombo.blockSignals(False)
            self.rebuild();self.refresh_reports()
        except (ResearchError,PortfolioError) as error:self.statusLabel.setText(str(error))

    def current_positions(self):
        # 操作时重读，避免打开后持仓被修改仍导出旧股数。
        positions=self.panel.store.load_all();scope=self.scopeCombo.currentData()
        return [p for p in positions if not scope or f'{p.position_id}|{p.code}'==scope]

    def collect(self,private):
        positions=self.current_positions();cache={};alerts={};options={};missing=[];risk_available=self.panel.risk.store is not None
        for p in positions:
            for kind in ('announcement','news'):cache[(p.code,kind)]=self.store.cached(p.code,kind)
            if private:
                key=(p.position_id,p.code)
                if risk_available:
                    try:
                        alerts[key]=self.panel.risk.store.records(*key,limit=3)
                        options[key]=self.panel.risk.store.load_settings(*key)
                    except RiskError:
                        risk_available=False;missing.append(key)
                else:missing.append(key)
        return build_packet(positions,self.panel.quotes,self.panel.source,cache,now=time.time(),include_private=private,
                            failed_codes=self.panel.failed_codes,alerts=alerts,risk_options=options,risk_missing=missing,failure_counts=self.panel.risk.failures)

    def rebuild(self,*_args):
        try:
            self.overviewText.setPlainText(self.collect(True)['body'])
            self.draft=self.collect(self.privateCheck.isChecked())
            self.promptText.setPlainText(self.draft['prompt'])
            self.statusLabel.setText('资料已在本机整理，未上传。复制后由你决定是否粘贴给ChatGPT。')
            self.render_articles()
        except (ResearchError,PortfolioError) as error:
            self.draft=None;self.promptText.clear();self.overviewText.setPlainText(str(error));self.statusLabel.setText(str(error))

    def fetch(self,force=False):
        if self.busy:
            self.statusLabel.setText('已有公开资料请求在后台进行，请稍候；其余功能可继续操作。');return
        try:
            jobs=[];now=time.time()
            for p in self.current_positions():
                for kind in ('announcement','news'):
                    _,state=self.store.cached(p.code,kind)
                    if state and now-state['last_try']<60:continue
                    if not force and state and not state['error'] and state['last_success'] is not None and now-state['last_success']<TTL:continue
                    jobs.append((p.code,p.name,kind))
            if not jobs:
                self.statusLabel.setText('使用现有缓存；每来源每股票至少间隔60秒，默认6小时后自动更新。');self.render_articles();return
            self.busy=True;self.statusLabel.setText('正在后台获取公开资料；不会上传持仓或提醒设置。')
            def worker():
                errors=[]
                for index,(code,name,kind) in enumerate(jobs):
                    try:
                        try:
                            rows=fetch_announcements(code) if kind=='announcement' else fetch_news(code,name)
                            self.store.record_fetch(code,kind,rows,now=time.time())
                        except ResearchError as error:
                            self.store.record_fetch(code,kind,[],now=time.time(),error=str(error));errors.append(code+' '+str(error))
                        except Exception:
                            message='来源解析失败，未使用伪造数据，旧缓存保留。'
                            self.store.record_fetch(code,kind,[],now=time.time(),error=message);errors.append(code+' '+message)
                    except Exception:
                        errors.append(code+' 本地保存或来源处理失败，未改动持仓。')
                    try:self.progress.emit(f'后台获取 {index+1}/{len(jobs)} 项；可继续操作。')
                    except RuntimeError:return
                try:self.fetched.emit(errors)
                except RuntimeError:pass
            threading.Thread(target=worker,daemon=True).start()
        except (ResearchError,PortfolioError) as error:self.statusLabel.setText(str(error))

    def on_fetched(self,errors):
        self.busy=False;self.rebuild()
        self.statusLabel.setText(('部分获取失败；旧缓存的原获取时间保留。'+('；'.join(errors)) if errors else '本次公开资料获取完成；不保证完整覆盖，发布时间与获取时间分开显示。')[:500])

    def render_articles(self,*_args):
        try:
            self.article_rows=[];messages=[];now=time.time()
            for p in self.current_positions():
                for kind in ('announcement','news'):
                    rows,state=self.store.cached(p.code,kind)
                    state_text=source_status(state,now)
                    messages.append(p.code+' '+('公告' if kind=='announcement' else '新闻')+'：'+state_text)
                    for row in rows:
                        if self.articleFilter.currentIndex()==0 or row['category']==self.articleFilter.currentText():
                            self.article_rows.append(dict(row,status=state_text))
            self.article_rows.sort(key=lambda r:r['published_epoch'],reverse=True)
            table=self.articlesTable;table.blockSignals(True);table.clear();table.setColumnCount(5)
            table.setHorizontalHeaderLabels(['股票 / 类型','标题','发布时间','来源','获取时间'])
            table.setRowCount(len(self.article_rows))
            for i,r in enumerate(self.article_rows):
                for j,t in enumerate((r['code']+'\n'+r['category'],r['title'],r['published'],r['source'],clock_text(r['fetched']))):
                    table.setItem(i,j,QTableWidgetItem(t))
                table.setRowHeight(i,58)
            for j,w in enumerate((155,370,180,220,190)):table.setColumnWidth(j,w)
            table.blockSignals(False)
            self.articleDetails.setPlainText('\n'.join(messages) or '无选中股票。')
            if self.article_rows:table.setCurrentCell(0,0);self.select_article(0)
        except (ResearchError,PortfolioError) as error:self.statusLabel.setText(str(error))

    def select_article(self,row,*_args):
        if not 0<=row<len(self.article_rows):return
        r=self.article_rows[row]
        self.articleDetails.setPlainText(r['status']+'\n'+r['title']+'\n来源：'+r['source']+'\n发布时间：'+r['published']
            +'\n获取时间：'+clock_text(r['fetched'])+'\n原文：'+r['url']+'\n摘要：'+(r['summary'] or '来源未提供；没有自动读取全文或推断内容。'))

    def open_article(self):
        i=self.articlesTable.currentRow()
        if not 0<=i<len(self.article_rows):return
        url=self.article_rows[i]['url']
        if safe_link(url):QDesktopServices.openUrl(QUrl(url))
        else:self.statusLabel.setText('原文链接未通过安全校验，未打开。')

    def copy_prompt(self):
        try:
            # 对应当前已预览的固定快照；复制不发任何网络请求。
            self.rebuild()  # 复制时重读持仓并重新检查行情年龄，不能导出过时的新鲜度判断。
            if self.draft is None:raise ResearchError('请先整理有效分析资料。')
            packet_id=self.store.save_packet(self.draft)
            QApplication.clipboard().setText(self.draft['prompt'])
            self.refresh_reports(packet_id)
            self.statusLabel.setText('资料已复制，'+('包含你主动选择的私人持仓。' if self.draft['private'] else '未包含私人持仓。')+'请自行粘贴到ChatGPT；本软件未上传。')
        except ResearchError as error:self.statusLabel.setText(str(error))

    def open_chatgpt(self):
        QDesktopServices.openUrl(QUrl('https://chatgpt.com/'))

    def refresh_reports(self,preferred=None):
        try:
            current=preferred if type(preferred) is int else self.packetCombo.currentData()
            self.packetCombo.clear()
            self.packetCombo.setPlaceholderText('请先复制分析资料，再选择对应时间导入')
            for p in self.store.packets():
                scope='、'.join(s['code'] for s in json.loads(p['scope_json']))
                self.packetCombo.addItem(clock_text(p['created'])+' '+scope+('（含私人持仓）' if p['private'] else '（不含私人持仓）'),p['id'])
            index=self.packetCombo.findData(current)
            if index>=0:self.packetCombo.setCurrentIndex(index)
            self.report_rows=self.store.reports();table=self.reportsTable
            table.blockSignals(True);table.clear();table.setColumnCount(4)
            table.setHorizontalHeaderLabels(['报告名称','对应股票','用户手动导入时间','数据时间'])
            table.setRowCount(len(self.report_rows))
            for i,r in enumerate(self.report_rows):
                texts=(r['title'],'、'.join(p['code'] for p in json.loads(r['scope_json'])),clock_text(r['created']),r['data_time'])
                for j,text in enumerate(texts):table.setItem(i,j,QTableWidgetItem(text))
                table.setRowHeight(i,52)
            for j,w in enumerate((200,220,200,380)):table.setColumnWidth(j,w)
            table.blockSignals(False);self.reportDetails.clear()
            if self.report_rows:table.setCurrentCell(0,0);self.select_report(0)
        except ResearchError as error:self.statusLabel.setText(str(error))

    def import_report(self):
        try:
            packet=self.packetCombo.currentData()
            self.store.import_report(packet,self.reportTitleEdit.text(),self.importText.toPlainText(),now=time.time())
            self.importText.clear();self.refresh_reports();self.statusLabel.setText('已保存用户手动导入报告；内容未经独立核验，未修改持仓或执行交易。')
        except ResearchError as error:self.statusLabel.setText(str(error))

    def select_report(self,row,*_args):
        self.reportDetails.setPlainText(report_text(self.report_rows[row]) if 0<=row<len(self.report_rows) else '')

    def copy_report(self):
        i=self.reportsTable.currentRow()
        if 0<=i<len(self.report_rows):QApplication.clipboard().setText(report_text(self.report_rows[i]))

    def delete_report(self):
        i=self.reportsTable.currentRow()
        if not 0<=i<len(self.report_rows):return
        r=self.report_rows[i]
        if not self.panel._question('再次确认删除报告','只删除选中AI历史报告，不能恢复；不影响持仓、提醒或其他报告。确认删除？'):return
        try:self.store.delete_report(r['id']);self.refresh_reports()
        except ResearchError as error:self.statusLabel.setText(str(error))
