"""A股同一行情快照的参考估值与新鲜度；没有网络或交易能力。
Added 2026-10-19, a-share-ai-butler; Apache-2.0.
"""
from datetime import datetime,timedelta,time as walltime
from stockwidget.core.portfolio import money,PortfolioError
from stockwidget.core.window_rules import QUOTE_TIMEZONE,quote_timestamp

SOURCES={'sina':'新浪财经','eastmoney':'东方财富'}
# 2026交易所全年休市公告；周末始终休市，调休工作日不作为交易日。
HOLIDAYS=((1,1,1,3),(2,15,2,23),(4,4,4,6),(5,1,5,5),(6,19,6,21),(9,25,9,27),(10,1,10,7))


def trading_day(day):
    if day.year!=2026:return None
    if day.weekday()>=5:return False
    return not any((m,d)<=(day.month,day.day)<=(em,ed) for m,d,em,ed in HOLIDAYS)


def market_phase(now):
    local=datetime.fromtimestamp(now,QUOTE_TIMEZONE);day=trading_day(local.date());t=local.time()
    if day is None:return 'unknown'
    if not day:return 'closed'
    if walltime(9,15)<=t<=walltime(11,30) or walltime(13)<=t<walltime(15):return 'session'
    if walltime(11,30)<t<walltime(13):return 'break'
    return 'closed'


def last_completed_day(now):
    local=datetime.fromtimestamp(now,QUOTE_TIMEZONE)
    if trading_day(local.date()) is None:return None
    day=local.date()
    if local.time()<walltime(15):day-=timedelta(days=1)
    for _ in range(20):
        if trading_day(day):return day.isoformat()
        day-=timedelta(days=1)
    return None


def quote_view(quote,*,now,source='sina',failed=False,code=None):
    q=quote or {};stamp=quote_timestamp(q);error=q.get('_last_error','')
    try:price=money(q.get('current_price'));price=price if price>0 else None
    except PortfolioError:price=None
    if code and q.get('_code') not in (None,code):price=None
    source=q.get('_source',source)
    if stamp is not None and stamp>now+60:price=None;error='行情时间异常，不能使用未来价格'
    phase=market_phase(now);age=now-stamp if stamp is not None else None
    close=q.get('_price_kind')=='close' and q.get('_close_date')
    live=bool(price and stamp and phase=='session' and -60<=age<=120 and not failed and not error and not close)
    if price is None:state='暂无可用行情';kind='missing'
    elif close:
        kind='close';expected=last_completed_day(now)
        state='最近交易日收盘价（非实时）' if expected==q['_close_date'] else '历史收盘参考价（非实时；可能停牌或数据延迟）'
        if expected and expected!=q['_close_date']:state+='；已过期'
        if phase=='session':state+='；当前交易时段仅有旧收盘参考'
        if phase=='unknown':state+='；交易日历未覆盖'
    elif live:state='交易时段最近可得行情（可能延迟，不保证实时）';kind='latest'
    elif stamp is None:state='最后有效价格；时间未知，无法判断是否过期（非实时）';kind='reference'
    else:
        kind='reference';state='最后有效行情（非实时）'
        if age>120:state+='；已过期'
        if phase=='break':state+='；午间休市'
        elif phase=='closed':state+='；收盘/休市，尚未核实收盘价'
        elif phase=='unknown':state+='；交易日历未覆盖'
    if failed or error:state+='；行情更新失败，保留最后有效数据' if price else '；行情更新失败'
    if error:state+='；原因：'+str(error)[:160]
    return dict(available=price is not None,live=live,price=price,timestamp=stamp,kind=kind,status=state,
                source=source,source_name=SOURCES.get(source,'未知来源'),close_date=q.get('_close_date'),error=error)
