"""人工报告摘要与不可变资料关联；只读比较，不生成AI结论。"""
import hashlib
import json
import re
from decimal import Decimal,InvalidOperation
from stockwidget.core.quote_snapshot import quote_view
from stockwidget.core.portfolio import PortfolioError,validate_sell_quantity,a_share_code

LABELS=('当前建议','主要原因','行动条件','主要风险','数据截至')
CHOICES={'观望','继续持有','考虑减仓','考虑买入'}


def extract_summary(content):
    matches=list(re.finditer(r'【(当前建议|主要原因|行动条件|主要风险|数据截至|操作思路)】',content))
    if len(matches)<5 or matches[0].start()>100 or [m[1] for m in matches[:5]]!=list(LABELS):return None
    fields={}
    for i,m in enumerate(matches[:5]):
        end=matches[i+1].start() if i+1<len(matches) else len(content)
        text=content[m.end():end].strip().strip('*').strip()
        if i==4:text=re.split(r'\n\s*\n|\n\s*(?:#{1,6}\s*)?(?:\*\*)?(?:详细|一[、.．]\s*研究身份)',text,maxsplit=1)[0].strip()
        fields[m[1]]=text
    if not re.match(r'^(?:观望|继续持有|考虑减仓|考虑买入)(?=$|[（(，,:：；;。\s])',fields['当前建议'].strip('*').strip()) or not all(fields.values()):return None
    if len(matches)>5 and matches[5][1]=='操作思路':
        end=matches[6].start() if len(matches)>6 else len(content)
        action=content[matches[5].end():end].strip()
        fields['操作思路']=re.split(r'\n\s*\n|\n\s*(?:#{1,6}\s*)?(?:\*\*)?(?:详细|一[、.．])',action,maxsplit=1)[0].strip()
    return fields


def summary_text(fields):
    order=('当前建议','操作思路','主要原因','行动条件','主要风险','数据截至')
    return '\n\n'.join('【'+label+'】'+fields[label] for label in order if fields.get(label))


def checked_action(fields,row,positions,quotes,source,now,*,unchanged):
    """只读校验行动摘要；不采纳数量，不触发任何持仓写入或交易。"""
    action=fields.get('操作思路','')
    if not action or not re.search(r'\d+\s*股',action):return fields
    result=dict(fields)
    def blocked(reason):
        result['操作思路']='数量方案未通过校验：'+reason+'。请重新分析；原文保留在展开区，不作为可执行建议。'
        return result
    if not unchanged:return blocked('原资料已变化或无法核验')
    if not row.get('private'):return blocked('原资料未授权包含私人持仓')
    entries=list(re.finditer(r'(卖出|买入)\s+([0-9]{6}\.(?:SH|SZ|BJ))\s+([0-9]+)\s*股',action))
    # 拒绝自然语言中无法可靠区分操作对象、重复数量或保留数量的混杂表达。
    if not entries or len(entries)!=len(re.findall(r'\d+\s*股',action)):
        return blocked('股数格式无法可靠识别，请使用“卖出 股票代码.市场 整数股”格式')
    saved={p.code:p for p in positions if not getattr(p,'candidate',False)}
    try:scope={p['code'] for p in json.loads(row['scope_json'])}
    except (ValueError,TypeError,KeyError):return blocked('原股票范围无法核验')
    sold={};buy_cost=Decimal(0);has_buy=False
    try:budget=Decimal(json.loads(row.get('context_json') or '{}').get('buy_budget','0'))
    except (ValueError,TypeError,InvalidOperation):return blocked('原买入预算无法核验')
    if not budget.is_finite() or budget<0:return blocked('原买入预算无效')
    for entry in entries:
        direction,code,raw=entry.groups()
        if len(raw)>10 or int(raw)>1_000_000_000:return blocked('股数超出支持范围')
        try:a_share_code(code)
        except PortfolioError:return blocked('不是支持的A股代码')
        if code not in scope:return blocked('操作股票不在原资料范围，请先添加候选并重新分析')
        view=quote_view(quotes.get(code) or {},now=now,source=source,code=code,
                        failed=bool((quotes.get(code) or {}).get('_last_error')))
        if not view['live']:return blocked('操作股票缺少现时有效行情；收盘价仅供参考')
        if direction=='买入':
            buy_cost+=view['price']*int(raw);has_buy=True
            if budget<=0 or buy_cost>budget:return blocked('买入参考金额超过原资料中本人填写的预算，或未提供预算；不计预计卖出款')
            continue
        p=saved.get(code)
        if p is None:return blocked('该股不是已保存的本地持仓')
        if code=='001330.SZ' and p.locked<200:return blocked('博纳永久锁定约束与本地数据冲突，需本人核对')
        sold[code]=sold.get(code,0)+int(raw)
        try:validate_sell_quantity(p,sold[code],code=code)
        except PortfolioError:return blocked('累计卖出数量超过该股票策略仓或输入非法，锁定仓受保护')
    result['操作思路']=action+'\n仅核对本地策略仓上限及已填写预算；券商可卖数量、资金、费用、申报数量规则和权限仍须本人核对。'
    if has_buy:result['操作思路']+='买入参考金额按资料价估算，未验证实际成交或申报合规，不可直接照单操作。'
    return result


def packet_context(positions,quotes,source,now,*,scope_all=False):
    result={'version':1,'scope_all':scope_all,'positions':{},'quotes':{}}
    for p in positions:
        key=str(p.position_id)+'|'+p.code
        result['positions'][key]=p.version
        q=quotes.get(p.code) or {};view=quote_view(q,now=now,source=source,failed=bool(q.get('_last_error')),code=p.code)
        data={name:q.get(name) for name in ('current_price','prev_close','timestamp','date','time','deals_vol','deals_amt','high_price','low_price','_close_date','_price_kind')}
        data.update(source=view['source'],kind=view['kind'],status=view['status'])
        result['quotes'][key]=hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()
    return result


def evidence_context(positions,cache,now):
    result={}
    for p in positions:
        entries=[]
        for kind in ('announcement','news'):
            rows,state=cache.get((p.code,kind),([],None));quality='missing'
            if state:
                success=state.get('last_success')
                quality='error:'+str(state.get('error')) if state.get('error') else 'stale' if success is None or now-success>21600 or now<success-60 else 'available'
            entries.append((kind,quality,[{k:r.get(k) for k in ('item_id','title','published','source','url','summary')} for r in rows]))
        result[p.code]=hashlib.sha256(json.dumps(entries,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    return result


def report_status(row,positions,quotes,source,*,now,evidence=None):
    raw=row.get('context_json')
    if not raw:return '旧报告缺少行情/持仓快照，无法核验；建议重新分析。'
    try:
        saved=json.loads(raw);keys=set(saved['positions'])
        universe=saved.get('universe','holdings')
        positions=[p for p in positions if universe=='combined' or bool(getattr(p,'candidate',False))==(universe=='candidates')]
        subset=[p for p in positions if str(p.position_id)+'|'+p.code in keys]
        if len(subset)!=len(keys):return '数据已变化，建议重新分析（股票已修改或归档）；原报告未改动。'
        if saved.get('scope_all') and len(positions)!=len(subset):return '数据已变化，建议重新分析（全部持仓范围变化）；原报告未改动。'
        current=packet_context(subset,quotes,source,now,scope_all=saved.get('scope_all',False))
        for field in ('universe','purpose','buy_budget'):
            if field in saved:current[field]=saved[field]
        if 'evidence' in saved:current['evidence']=({code:evidence.get(code) for code in saved['evidence']} if evidence is not None else saved['evidence'])
        if saved!=current:return '数据已变化，建议重新分析（行情、公开资料状态或持仓变化）；原报告未改动。'
        return '仅对应下列原始股票及数据时间；用户导入内容未经核验，非程序的实时建议。'
    except (ValueError,TypeError,KeyError):return '旧报告快照无法核验，建议重新分析；原文保留。'
