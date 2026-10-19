"""匿名公开公告/新闻适配；只发股票代码，无登录、Cookie持久化或交易。
Added 2026-10-19, a-share-ai-butler; Apache-2.0.
"""
from datetime import datetime,timedelta
from html.parser import HTMLParser
from urllib.parse import urlparse,urljoin,unquote
import re
import requests

from stockwidget.core.portfolio import a_share_code
from stockwidget.core.window_rules import QUOTE_TIMEZONE

CNINFO='https://www.cninfo.com.cn'
SINA='https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllNewsStock.php'
TTL=21600  # 公开元数据6小时缓存；不是实时新闻承诺。


class ResearchError(ValueError):
    pass


def safe_link(value):
    value=str(value or '').strip()
    try:
        p=urlparse(value)
        allowed={'static.cninfo.com.cn','www.cninfo.com.cn','finance.sina.com.cn','cj.sina.cn','t.cj.sina.cn'}
        if p.scheme!='https' or p.hostname not in allowed or p.username or p.password or p.port not in (None,443):
            return False
        path=unquote(unquote(p.path))
        return not any(x in path for x in ('..','\\','\x00')) and not any(ord(c)<32 for c in value+path)
    except ValueError:
        return False


class Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.parts=[];self.ignore=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.ignore+=1
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.ignore=max(0,self.ignore-1)
    def handle_data(self,data):
        if not self.ignore:self.parts.append(data)


def plain(value,limit=500):
    p=Plain();p.feed(str(value or ''));return ' '.join(' '.join(p.parts).split())[:limit]


def category(title):
    if any(k in title for k in ('年度报告','季度报告','半年度报告','业绩预告','业绩快报')):return '财务公告'
    if any(k in title for k in ('异常波动','诉讼','退市','风险','担保','亏损','减持')):return '风险关注（仅标题）'
    return '公司公告'


def parse_announcements(payload,code):
    code=a_share_code(code);rows=[]
    if not isinstance(payload,dict) or 'announcements' not in payload:raise ResearchError('巨潮公告响应格式变化或访问受限。')
    raw=payload['announcements']
    if raw is None and payload.get('totalAnnouncement',0)==0:return []
    if not isinstance(raw,list):raise ResearchError('巨潮公告列表无效。')
    for entry in raw:
        if str(entry.get('secCode'))!=code[:6]:continue
        try:
            published=datetime.fromtimestamp(float(entry['announcementTime'])/1000,QUOTE_TIMEZONE)
            link=urljoin('https://static.cninfo.com.cn/',str(entry['adjunctUrl']))
            if (not safe_link(link) or urlparse(link).hostname!='static.cninfo.com.cn'
                    or not urlparse(link).path.startswith('/finalpage/') or not urlparse(link).path.lower().endswith('.pdf')):continue
            title=plain(entry.get('announcementTitle'),300)
            if not title or not str(entry.get('announcementId','')):continue
            rows.append(dict(code=code,kind='announcement',item_id=str(entry['announcementId']),title=title,
                published=published.strftime('%Y-%m-%d')+'（仅披露日期）',published_epoch=published.timestamp(),
                source='巨潮资讯（公司披露）',url=link,summary=plain(entry.get('announcementContent'),500),category=category(title)))
        except (KeyError,TypeError,ValueError,OverflowError,OSError):continue
    if raw and not rows:raise ResearchError('公告列表未通过股票代码/时间/链接校验，未显示为有效数据。')
    return rows


class NewsList(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.prefix='';self.link=None;self.title=[];self.rows=[]
    def handle_starttag(self,tag,attrs):
        if tag=='br':self.prefix=''
        if tag=='a':self.link=dict(attrs).get('href');self.title=[]
    def handle_data(self,data):
        if self.link:self.title.append(data)
        else:self.prefix=(self.prefix+data)[-400:]
    def handle_endtag(self,tag):
        if tag=='a' and self.link:
            dates=re.findall(r'(\d{4}-\d\d-\d\d)\s+(\d\d:\d\d)',self.prefix.replace('\xa0',' '))
            if dates:self.rows.append((' '.join(dates[-1]),self.link,' '.join(self.title)))
            self.link=None;self.title=[]


def parse_news(text,code,name):
    code=a_share_code(code)
    match=re.search(r'<div\s+class=[\"\x27]datelist[\"\x27][^>]*>(.*?)</div>',text,re.S|re.I)
    if not match:raise ResearchError('新浪个股资讯不可用或页面结构变化；没有以空列表冒充成功。')
    parser=NewsList();parser.feed(match[1]);rows=[]
    for date,link,title in parser.rows:
        title=plain(title,300)
        if code[:6] not in title and (not name or name not in title):continue
        if not safe_link(link) or urlparse(link).hostname not in {'finance.sina.com.cn','cj.sina.cn','t.cj.sina.cn'}:continue
        try:published=datetime.strptime(date,'%Y-%m-%d %H:%M').replace(tzinfo=QUOTE_TIMEZONE)
        except ValueError:continue
        rows.append(dict(code=code,kind='news',item_id=link,title=title,published=date+'（北京时间）',
            published_epoch=published.timestamp(),source='新浪财经个股资讯（聚合，原作者见原文）',url=link,summary='',category='相关新闻'))
    return rows[:20]


def _request(session,method,url,**kwargs):
    try:
        with session.request(method,url,timeout=(4,12),stream=True,allow_redirects=False,**kwargs) as response:
            response.raise_for_status()
            if response.status_code!=200:raise ResearchError('公开来源重定向或访问受限，未尝试绕过。')
            parts=[];size=0
            for part in response.iter_content(65536):
                size+=len(part)
                if size>2*1024*1024:raise ResearchError('公开来源响应过大，已停止读取。')
                parts.append(part)
            return b''.join(parts)
    except requests.RequestException:
        raise ResearchError('网络连接失败或来源拒绝访问；显示本机旧缓存（如有）。') from None


def fetch_announcements(code):
    import json
    code=a_share_code(code)
    with requests.Session() as session:
        session.headers.update({'User-Agent':'Mozilla/5.0','Referer':CNINFO+'/'})
        try:
            matches=json.loads(_request(session,'POST',CNINFO+'/new/information/topSearch/query',data={'keyWord':code[:6],'maxNum':5}))
            if not isinstance(matches,list):raise ResearchError('巨潮证券查询响应格式变化或访问受限。')
            matched=next((x for x in matches if isinstance(x,dict) and str(x.get('code'))==code[:6] and x.get('category')=='A股'),None)
            if not matched:raise ResearchError('巨潮未找到对应A股代码；不使用相似股票公告。')
            now=datetime.now(QUOTE_TIMEZONE)
            data={'pageNum':1,'pageSize':30,'tabName':'fulltext','column':'szse','stock':code[:6]+','+str(matched['orgId']),
                  'seDate':(now-timedelta(days=365)).strftime('%Y-%m-%d')+'~'+now.strftime('%Y-%m-%d'),'isHLtitle':'false'}
            payload=json.loads(_request(session,'POST',CNINFO+'/new/hisAnnouncement/query',data=data))
            return parse_announcements(payload,code)
        except ResearchError:raise
        except (ValueError,TypeError,KeyError):
            raise ResearchError('巨潮响应无法解析，可能访问受限；未绕过登录或验证。') from None


def fetch_news(code,name):
    code=a_share_code(code)
    with requests.Session() as session:
        session.headers.update({'User-Agent':'Mozilla/5.0'})
        raw=_request(session,'GET',SINA,params={'symbol':code[-2:].lower()+code[:6],'Page':1})
        return parse_news(raw.decode('gb18030',errors='replace'),code,name)
