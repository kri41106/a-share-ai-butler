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
from stockwidget.core.briefing import BriefingStore,build_packet,source_status,report_text,scope_caption
from stockwidget.core.candidates import CandidateStore,research_universe
from stockwidget.core.quote_snapshot import quote_view
from stockwidget.core.window_rules import quote_timestamp
from stockwidget.core.advice import extract_summary,summary_text,report_status,evidence_context
from stockwidget.data.research import fetch_announcements,fetch_news,ResearchError,safe_link,TTL
from stockwidget.ui.controls.style import apply_portfolio_theme


class BriefingCenter(QWidget):
    fetched=Signal(object)
    progress=Signal(str)

    def __init__(self,panel):
        super().__init__(panel,Qt.Window)
        self.panel=panel
        self.store=panel.quote_store or BriefingStore(panel.store.path.parent/'research.sqlite3')
        self.candidates=CandidateStore(self.store)
        self.candidate_quotes=self.store.load_quotes(panel.source);self.candidate_source=panel.source
        from stockwidget.ui.portfolio import QuotePoller
        self.candidate_poller=QuotePoller(self)
        self.candidate_poller.finished.connect(self.accept_candidate_quotes)
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
               'privateCheck','promptText','packetCombo','reportTitleEdit','importText','reportsTable','reportDetails',
               'privacyStateLabel','suggestionText','reportStateLabel','expandReportCheck','generatedAtEdit',
               'candidateNameEdit','candidateCodeEdit','candidatesTable','taskCombo','focusEdit')
        for name in names:setattr(self,name,self.form.findChild(QObject,name))
        self.statusLabel.setMaximumHeight(85)
        self.scopeCombo.currentIndexChanged.connect(self.rebuild)
        self.taskCombo.currentIndexChanged.connect(self.task_changed)
        self.focusEdit.editingFinished.connect(self.rebuild)
        self.privateCheck.setChecked(self.store.private_preference())
        self.privateCheck.toggled.connect(self.private_changed)
        self.expandReportCheck.toggled.connect(self.reportDetails.setVisible)
        self.reportDetails.hide()
        self.articleFilter.currentIndexChanged.connect(self.render_articles)
        self.articlesTable.currentCellChanged.connect(self.select_article)
        self.reportsTable.currentCellChanged.connect(self.select_report)
        callbacks={'reloadPositionsButton':self.reload_positions,'fetchButton':lambda:self.fetch(True),
                   'openArticleButton':self.open_article,'buildOverviewButton':self.rebuild,'buildPromptButton':self.rebuild,
                   'copyPromptButton':self.copy_prompt,'openChatGPTButton':self.open_chatgpt,'refreshQuotesButton':self.refresh_quotes,
                   'importReportButton':self.import_report,'refreshReportsButton':self.refresh_reports,
                   'copyReportButton':self.copy_report,'deleteReportButton':self.delete_report,
                   'addCandidateButton':self.add_candidate,'archiveCandidateButton':self.archive_candidate,
                   'analyzeCandidateButton':self.analyze_candidate,'discoverButton':self.discover,
                   'compareCandidatesButton':self.compare_candidates,'refreshCandidatesButton':self.refresh_research}
        for name,callback in callbacks.items():self.form.findChild(QObject,name).clicked.connect(callback)
        self.fetched.connect(self.on_fetched)
        self.progress.connect(self.statusLabel.setText)

    def open_page(self,index,*,auto_fetch=True):
        self.privateCheck.setChecked(self.store.private_preference())
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
            candidates=[p for p in self.candidates.load_all() if p.code not in {h.code for h in self.positions}]
            for p in candidates:self.scopeCombo.addItem('候选：'+p.name+' '+p.code,f'{p.position_id}|{p.code}')
            if candidates:
                self.scopeCombo.addItem('全部候选（未登记持仓）','candidates')
                self.scopeCombo.addItem('持仓 + 候选对照','combined')
            selected_key=f'{selected.position_id}|{selected.code}' if selected else ''
            preferred=selected_key if prefer_selected or previous is None else previous
            index=self.scopeCombo.findData(preferred)
            self.scopeCombo.setCurrentIndex(index if index>=0 else 0);self.scopeCombo.blockSignals(False)
            self.rebuild();self.refresh_reports();self.render_candidates()
        except (ResearchError,PortfolioError) as error:self.statusLabel.setText(str(error))

    def current_positions(self):
        # 操作时重读，避免打开后持仓被修改仍导出旧股数。
        positions=self.panel.store.load_all();scope=self.scopeCombo.currentData()
        candidates=self.candidates.load_all();universe=research_universe(positions,candidates)
        if scope=='combined':return universe
        if scope=='candidates':return [p for p in universe if getattr(p,'candidate',False)]
        if not scope:return positions
        return [p for p in universe if f'{p.position_id}|{p.code}'==scope]

    def quote_snapshot(self):
        if self.candidate_source!=self.panel.source:
            self.candidate_quotes=self.store.load_quotes(self.panel.source);self.candidate_source=self.panel.source
        return dict(self.candidate_quotes,**self.panel.display_quotes())

    def render_candidates(self):
        self.candidate_rows=[p for p in self.candidates.load_all() if p.code not in {h.code for h in self.panel.store.load_all()}]
        quotes=self.quote_snapshot();table=self.candidatesTable;table.setColumnCount(3)
        table.setHorizontalHeaderLabels(['候选股票（非持仓）','参考价格','来源 / 时间 / 状态']);table.setRowCount(len(self.candidate_rows))
        for i,p in enumerate(self.candidate_rows):
            q=quotes.get(p.code) or {};view=quote_view(q,now=time.time(),source=self.panel.source,code=p.code)
            for j,text in enumerate((p.name+' '+p.code,str(view['price'])+' 元' if view['available'] else '暂无可用行情',
                view['source_name']+'\n'+(clock_text(view['timestamp']) if view['timestamp'] else '时间未知')+'\n'+view['status'])):
                item=QTableWidgetItem(text);item.setToolTip(text);table.setItem(i,j,item)
            table.setRowHeight(i,90)
        for j,width in enumerate((220,120,600)):table.setColumnWidth(j,width)

    def add_candidate(self):
        try:
            self.candidates.save(self.candidateNameEdit.text(),self.candidateCodeEdit.text(),held_codes={p.code for p in self.panel.store.load_all()})
            self.candidateNameEdit.clear();self.candidateCodeEdit.clear();self.reload_positions()
            self.statusLabel.setText('已加入独立候选清单，未登记买入。选择股票后刷新资料，再分析。')
        except (ResearchError,PortfolioError) as error:self.statusLabel.setText(str(error))

    def selected_candidate(self):
        i=self.candidatesTable.currentRow()
        if not 0<=i<len(self.candidate_rows):raise PortfolioError('请先在列表中选择一只候选股。')
        return self.candidate_rows[i]

    def archive_candidate(self):
        try:
            p=self.selected_candidate()
            if not self.panel._question('移除候选股票',f'仅从研究清单移除 {p.name} {p.code}；原报告及持仓不变。确认移除？'):return
            self.candidates.archive(p.code);self.reload_positions()
        except (ResearchError,PortfolioError) as error:self.statusLabel.setText(str(error))

    def analyze_candidate(self):
        try:
            p=self.selected_candidate();self.taskCombo.setCurrentIndex(0)
            self.scopeCombo.setCurrentIndex(self.scopeCombo.findData(f'{p.position_id}|{p.code}'))
            self.rebuild();self.tabs.setCurrentIndex(2)
        except (ResearchError,PortfolioError) as error:self.statusLabel.setText(str(error))

    def discover(self):
        self.taskCombo.setCurrentIndex(1);self.rebuild();self.tabs.setCurrentIndex(2)

    def compare_candidates(self):
        index=self.scopeCombo.findData('combined')
        if index<0:self.statusLabel.setText('请先添加至少一只候选股，再与持仓比较。');return
        self.scopeCombo.setCurrentIndex(index);self.taskCombo.setCurrentIndex(2);self.rebuild();self.tabs.setCurrentIndex(2)

    def task_changed(self,*_args):
        if self.taskCombo.currentIndex() in (1,2):
            index=self.scopeCombo.findData('combined')
            self.scopeCombo.setCurrentIndex(index if index>=0 else 0)
        self.rebuild()

    def refresh_research(self):
        self.refresh_quotes();self.fetch(True)

    def refresh_quotes(self):
        self.panel.refresh()
        candidates=[p for p in self.current_positions() if getattr(p,'candidate',False)]
        if self.tabs.currentIndex()==4:candidates=[p for p in research_universe(self.panel.store.load_all(),self.candidates.load_all()) if getattr(p,'candidate',False)]
        if candidates:self.candidate_poller.fetch({p.instrument()['market']+p.instrument()['code']:p.instrument() for p in candidates},self.panel.source,self.panel.generation)
        else:self.rebuild()

    def accept_candidate_quotes(self,payload):
        self.candidate_poller.busy=False
        keys,source,generation,ok,data,error=payload
        if source!=self.panel.source or generation!=self.panel.generation:return
        if self.candidate_source!=source:self.candidate_quotes=self.store.load_quotes(source);self.candidate_source=source
        try:
            for p in self.candidates.load_all():
                key=p.instrument()['market']+p.instrument()['code']
                if key not in keys:continue
                old=self.candidate_quotes.get(p.code) or {};q=(data or {}).get(key) if ok else None
                v=quote_view(q,now=time.time(),source=source,code=p.code)
                stamp=quote_timestamp(old)
                if q and q.get('_source') in (None,source) and v['available'] and (stamp is None or (v['timestamp'] is not None and v['timestamp']>=stamp)):
                    q=dict(q,_code=p.code,_source=source,_received_at=time.time());q.pop('_last_error',None)
                    self.candidate_quotes[p.code]=q
                elif old:self.candidate_quotes[p.code]=dict(old,_last_error=error or '候选行情缺失、代码异常或时间倒退，保留原快照')
                else:self.candidate_quotes[p.code]={'_code':p.code,'_source':source,'_last_error':error or '来源未返回有效候选行情'}
                if self.candidate_quotes[p.code].get('current_price'):self.store.cache_quote(p.code,source,self.candidate_quotes[p.code])
            self.render_candidates();self.rebuild()
        except (ResearchError,PortfolioError) as error:self.statusLabel.setText(str(error))

    def private_changed(self,checked):
        try:self.store.private_preference(checked)
        except ResearchError as error:self.statusLabel.setText(str(error))
        self.rebuild()

    def collect(self,private,*,positions=None,quotes=None,now=None):
        positions=self.current_positions() if positions is None else positions
        quotes=self.quote_snapshot() if quotes is None else quotes
        now=time.time() if now is None else now
        cache={};alerts={};options={};missing=[];risk_available=self.panel.risk.store is not None
        for p in positions:
            for kind in ('announcement','news'):cache[(p.code,kind)]=self.store.cached(p.code,kind)
            if private and not getattr(p,'candidate',False):
                key=(p.position_id,p.code)
                if risk_available:
                    try:
                        alerts[key]=self.panel.risk.store.records(*key,limit=3)
                        options[key]=self.panel.risk.store.load_settings(*key)
                    except RiskError:
                        risk_available=False;missing.append(key)
                else:missing.append(key)
        return build_packet(positions,quotes,self.panel.source,cache,now=now,include_private=private,
                            failed_codes=self.panel.failed_codes,alerts=alerts,risk_options=options,risk_missing=missing,failure_counts=self.panel.risk.failures,
                            scope_all=self.scopeCombo.currentData() in ('','candidates','combined'),
                            universe=('combined' if self.scopeCombo.currentData()=='combined' else 'candidates' if positions and all(getattr(p,'candidate',False) for p in positions) else 'holdings'),
                            purpose=('review','discovery','comparison')[self.taskCombo.currentIndex()],focus=self.focusEdit.text())

    def rebuild(self,*_args):
        try:
            self.panel._reload();self.panel.render()
            positions=self.current_positions();quotes=self.quote_snapshot();now=time.time()
            self.overviewText.setPlainText(self.collect(True,positions=positions,quotes=quotes,now=now)['body'])
            self.draft=self.collect(self.privateCheck.isChecked(),positions=positions,quotes=quotes,now=now)
            self.privacyStateLabel.setText(('已包含持仓：成本、数量、锁定/策略仓及私人提醒；' if self.draft['private'] else '未包含持仓：不导出成本、股数和私人提醒；')+'已记住选择，可随时关闭。')
            if positions and all(getattr(p,'candidate',False) for p in positions):self.privacyStateLabel.setText('本次仅候选股，没有登记持仓可包含；未生成成本、数量或盈亏。')
            self.promptText.setPlainText(self.draft['prompt'])
            self.statusLabel.setText('资料已在本机整理，未上传。复制后由你决定是否粘贴给ChatGPT。')
            self.render_articles();self.render_candidates()
        except (ResearchError,PortfolioError) as error:
            self.draft=None;self.promptText.clear();self.overviewText.setPlainText(str(error));self.statusLabel.setText(str(error))

    def fetch(self,force=False):
        if self.busy:
            self.statusLabel.setText('已有公开资料请求在后台进行，请稍候；其余功能可继续操作。');return
        try:
            jobs=[];now=time.time()
            positions=self.current_positions()
            if self.tabs.currentIndex()==4:positions=[p for p in research_universe(self.panel.store.load_all(),self.candidates.load_all()) if getattr(p,'candidate',False)]
            for p in positions:
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
                scope=scope_caption(p)
                self.packetCombo.addItem(clock_text(p['created'])+' '+scope+('（含私人持仓）' if p['private'] else '（不含私人持仓）'),p['id'])
            index=self.packetCombo.findData(current)
            if index>=0:self.packetCombo.setCurrentIndex(index)
            self.report_rows=self.store.reports();table=self.reportsTable
            table.blockSignals(True);table.clear();table.setColumnCount(4)
            table.setHorizontalHeaderLabels(['报告名称','对应股票','用户手动导入时间','数据时间'])
            table.setRowCount(len(self.report_rows))
            for i,r in enumerate(self.report_rows):
                texts=(r['title'],scope_caption(r),clock_text(r['created']),r['data_time'])
                for j,text in enumerate(texts):table.setItem(i,j,QTableWidgetItem(text))
                table.setRowHeight(i,52)
            for j,w in enumerate((200,220,200,380)):table.setColumnWidth(j,w)
            table.blockSignals(False);self.reportDetails.clear()
            if self.report_rows:table.setCurrentCell(0,0);self.select_report(0)
        except ResearchError as error:self.statusLabel.setText(str(error))

    def import_report(self):
        try:
            packet=self.packetCombo.currentData()
            self.store.import_report(packet,self.reportTitleEdit.text(),self.importText.toPlainText(),now=time.time(),generated_at=self.generatedAtEdit.text().strip())
            self.importText.clear();self.refresh_reports();self.statusLabel.setText('已保存用户手动导入报告；内容未经独立核验，未修改持仓或执行交易。')
        except ResearchError as error:self.statusLabel.setText(str(error))

    def select_report(self,row,*_args):
        if not 0<=row<len(self.report_rows):
            self.reportDetails.clear();self.suggestionText.clear();return
        record=self.report_rows[row]
        self.reportDetails.setPlainText(report_text(record))
        fields=extract_summary(record['content'])
        self.suggestionText.setPlainText('用户手动导入，未经核验\n'+summary_text(fields) if fields else '未可靠识别简明格式，以下保存并显示原文，未补造结论：\n'+record['content'])
        self.expandReportCheck.setChecked(fields is None)
        self.reportDetails.setVisible(self.expandReportCheck.isChecked())
        self.refresh_report_status()

    def refresh_report_status(self):
        i=self.reportsTable.currentRow()
        if not 0<=i<len(self.report_rows):return
        row=self.report_rows[i]
        try:
            positions=research_universe(self.panel.store.load_all(),self.candidates.load_all())
            cache={(p.code,k):self.store.cached(p.code,k) for p in positions for k in ('announcement','news')}
            state=report_status(row,positions,self.quote_snapshot(),self.panel.source,now=time.time(),evidence=evidence_context(positions,cache,time.time()))
            scope=scope_caption(row)
            self.reportStateLabel.setText(state+'\n原报告范围：'+scope+'；所用行情：'+row['data_time'])
        except (ResearchError,PortfolioError) as error:self.reportStateLabel.setText(str(error))

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
