"""人工报告摘要与不可变资料关联；只读比较，不生成AI结论。"""
import hashlib
import json
import re
from stockwidget.core.quote_snapshot import quote_view

LABELS=('当前建议','主要原因','行动条件','主要风险','数据截至')
CHOICES={'观望','继续持有','考虑减仓','考虑买入'}


def extract_summary(content):
    matches=list(re.finditer(r'【(当前建议|主要原因|行动条件|主要风险|数据截至)】',content))
    if len(matches)<5 or matches[0].start()>100 or [m[1] for m in matches[:5]]!=list(LABELS):return None
    fields={}
    for i,m in enumerate(matches[:5]):
        end=matches[i+1].start() if i<4 else len(content)
        text=content[m.end():end].strip().strip('*').strip()
        if i==4:text=re.split(r'\n\s*\n|\n\s*(?:#{1,6}\s*)?(?:\*\*)?(?:详细|一[、.．]\s*研究身份)',text,maxsplit=1)[0].strip()
        fields[m[1]]=text
    if not re.match(r'^(?:观望|继续持有|考虑减仓|考虑买入)(?=$|[（(，,:：；;。\s])',fields['当前建议'].strip('*').strip()) or not all(fields.values()):return None
    return fields


def summary_text(fields):
    return '\n'.join('【'+label+'】'+fields[label] for label in LABELS)


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
        for field in ('universe','purpose'):
            if field in saved:current[field]=saved[field]
        if 'evidence' in saved:current['evidence']=({code:evidence.get(code) for code in saved['evidence']} if evidence is not None else saved['evidence'])
        if saved!=current:return '数据已变化，建议重新分析（行情、公开资料状态或持仓变化）；原报告未改动。'
        return '仅对应下列原始股票及数据时间；用户导入内容未经核验，非程序的实时建议。'
    except (ValueError,TypeError,KeyError):return '旧报告快照无法核验，建议重新分析；原文保留。'
