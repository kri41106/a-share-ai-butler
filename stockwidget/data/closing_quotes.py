"""复用现有未复权日线确认最近收盘，不跨来源、不构造价格。"""
from datetime import datetime,time as walltime
import time
import requests
from stockwidget.core.quote_snapshot import market_phase,last_completed_day
from stockwidget.core.window_rules import QUOTE_TIMEZONE,quote_timestamp
from stockwidget.data.bars import request_sina_bars,request_eastmoney_bars


class ClosingQuotes:
    def __init__(self):self.cache={};self.checked={}
    def enrich(self,instruments,source,data,now=None):
        now=time.time() if now is None else now
        if market_phase(now) in ('session','break'):return data
        result=dict(data or {});expected=last_completed_day(now)
        for key,instrument in instruments.items():
            cache_key=(source,key);cached=self.cache.get(cache_key);old=result.get(key) or {}
            if not cached or (expected and cached['_close_date']!=expected):
                if now-self.checked.get(cache_key,-1e20)>=60:
                    self.checked[cache_key]=now
                    try:
                        bars=(request_eastmoney_bars if source=='eastmoney' else request_sina_bars)(instrument,'daily')
                        eligible=[]
                        for b in bars:
                            day=datetime.fromisoformat(b.time[:10]).date()
                            stamp=datetime.combine(day,walltime(15),QUOTE_TIMEZONE).timestamp()
                            if stamp<=now and b.close>0 and (not expected or day.isoformat()<=expected):eligible.append((stamp,b))
                        if eligible:
                            stamp,b=max(eligible,key=lambda x:x[0]);ordered=sorted((x for x in bars if x.time[:10]<b.time[:10]),key=lambda x:x.time)
                            cached=dict(current_price=b.close,prev_close=ordered[-1].close if ordered else 0,
                                timestamp=stamp,date=b.time[:10],time='15:00:00',deals_vol=b.volume,
                                _price_kind='close',_close_date=b.time[:10],_source=source)
                            self.cache[cache_key]=cached
                    except (requests.RequestException,ValueError,TypeError,KeyError,AttributeError):pass
            if cached:
                old_stamp=quote_timestamp(old);close_stamp=cached['timestamp']
                # 同日收盘后接口时间可能为15:35，保留该原始时间，价格以日线收盘核实。
                if old_stamp is None or old_stamp<=close_stamp or (old.get('date')==cached['_close_date'] and old.get('current_price')==cached['current_price']):
                    merged=dict(cached)
                    if old_stamp and old_stamp>=close_stamp and old.get('date')==cached['_close_date']:
                        merged.update(timestamp=old_stamp,time=old.get('time'),date=old.get('date'))
                    result[key]=merged
        return result
