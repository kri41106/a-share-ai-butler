"""独立A股研究清单；无持仓数量、成本、交易或模型权限。Apache-2.0。"""
from dataclasses import dataclass
import json
from stockwidget.core.portfolio import a_share_code,PortfolioError
from stockwidget.data.research import ResearchError

KEY='research_candidates_v1'


@dataclass(frozen=True)
class Candidate:
    name:str
    code:str
    version:int=1
    candidate:bool=True

    @property
    def position_id(self):
        return -(int(self.code[:6])*10+{'SH':1,'SZ':2,'BJ':3}[self.code[-2:]])

    def instrument(self):
        code,market=self.code.split('.')
        return {'code':code,'market':market.lower(),'name':self.name,
                'type':{'SH':'沪','SZ':'深','BJ':'京'}[market],'checked':True}


class CandidateStore:
    def __init__(self,research):self.research=research

    @staticmethod
    def _read(db):
        row=db.execute('SELECT value FROM preferences WHERE key=?',(KEY,)).fetchone()
        if row is None:return []
        try:
            data=json.loads(row[0]);seen=set()
            if not isinstance(data,list):raise ValueError
            for x in data:
                if a_share_code(x['code']) in seen or not isinstance(x['name'],str) or not 1<=len(x['name'])<=40:raise ValueError
                if type(x['version']) is not int or x['version']<1 or type(x['active']) is not bool:raise ValueError
                seen.add(x['code'])
            return data
        except (ValueError,TypeError,KeyError):raise ResearchError('候选清单格式异常，未重置或修改持仓。') from None

    def load_all(self):
        with self.research._db() as db:
            return [Candidate(x['name'],x['code'],x['version']) for x in self._read(db) if x['active']]

    def save(self,name,code,*,held_codes=()):
        code=a_share_code(code)
        if not isinstance(name,str):raise PortfolioError('股票名称必须为文字。')
        name=name.strip()
        if not 1<=len(name)<=40:raise PortfolioError('请填写不超过40字的股票名称。')
        if code in held_codes:raise PortfolioError('该股票已经在持仓中，请直接选择持仓分析。')
        with self.research._db() as db:
            rows=self._read(db);old=next((x for x in rows if x['code']==code),None)
            if old and old['active']:raise PortfolioError('该候选股票已存在，请直接选择分析；如需更正名称，先移除再添加。')
            if sum(x['active'] for x in rows)>=30:raise PortfolioError('候选清单最多30只，请先移除不再关注的股票。')
            if old:old.update(name=name,version=old['version']+1,active=True)
            else:rows.append(dict(name=name,code=code,version=1,active=True))
            db.execute('INSERT INTO preferences VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(KEY,json.dumps(rows,ensure_ascii=False)))

    def archive(self,code):
        code=a_share_code(code)
        with self.research._db() as db:
            rows=self._read(db);old=next((x for x in rows if x['code']==code and x['active']),None)
            if old is None:raise PortfolioError('候选股票已移除，请刷新清单。')
            old.update(active=False,version=old['version']+1)
            db.execute('UPDATE preferences SET value=? WHERE key=?',(json.dumps(rows,ensure_ascii=False),KEY))


def research_universe(holdings,candidates):
    held={p.code for p in holdings}
    return list(holdings)+[c for c in candidates if c.code not in held]
