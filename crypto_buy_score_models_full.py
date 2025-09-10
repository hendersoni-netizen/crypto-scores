#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Builds docs/data.json with price and 5 buy-signal models per symbol.
Also writes legacy `score` (M2) so old pages still render.
"""
import json, numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime, timezone, timedelta
import ccxt

EXCHANGE_ID = "binance"
SYMBOLS = ["BTC/USDT", "ETH/USDT", "ONDO/USDT"]
HOURS = 12

DOCS = Path("docs")
OUT = DOCS / "data.json"

def floor_to_step(dt, minutes):
    m=(dt.minute//minutes)*minutes
    return dt.replace(minute=m, second=0, microsecond=0)

def timeline(hours, step=15):
    end=floor_to_step(datetime.now(timezone.utc), step)
    start=end - timedelta(hours=hours)
    t=start; out=[]
    while t<=end:
        out.append(t.strftime("%Y-%m-%dT%H:%M:%SZ")); t+=timedelta(minutes=step)
    return out

def ema(s, p): return s.ewm(span=p, adjust=False).mean()

def atr(df, n=14):
    h,l,c=df["high"],df["low"],df["close"]
    pc=c.shift(1)
    tr=pd.concat([(h-l),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    return tr.rolling(n).mean()

def add_ind(df):
    c=df["close"]
    for col in ("open","high","low"): df[col]=df[col].fillna(c)
    df["ema20"]=ema(c,20); df["ema50"]=ema(c,50)
    df["atr14"]=atr(df,14)
    mid=c.rolling(20).mean()
    std=c.rolling(20).std(ddof=0)
    df["bb_low"]=mid-2*std; df["bb_high"]=mid+2*std
    df["pctB"]=(c-df["bb_low"])/((df["bb_high"]-df["bb_low"]).replace(0,np.nan))
    df["slope"]=df["ema20"]-df["ema20"].shift(1)
    df["trend"]=(df["ema20"]-df["ema50"])/(df["atr14"]+1e-12)
    return df.dropna().copy()

def smooth(v, up=0.45, down=0.12, cap_up=12, cap_dn=10):
    out=[]; prev=0.0
    for x in pd.Series(v).fillna(0):
        a=up if x>prev else down
        prev=prev + a*(x-prev)
        if out: prev=np.clip(prev, out[-1]-cap_dn, out[-1]+cap_up)
        prev=float(np.clip(prev,0,100)); out.append(prev)
    return out

def bin_to_15m(series_ts, values, labels):
    # map arbitrary timestamps into the label bins (ISO strings), keeping max
    idx_map={lbl:i for i,lbl in enumerate(labels)}
    out=[None]*len(labels)
    for t,v in zip(series_ts, values):
        key=t.strftime("%Y-%m-%dT%H:%M:%SZ")
        i=idx_map.get(key)
        if i is None: 
            # floor to 15m
            tt = t.replace(minute=(t.minute//15)*15, second=0, microsecond=0)
            key=tt.strftime("%Y-%m-%dT%H:%M:%SZ")
            i=idx_map.get(key)
        if i is not None:
            if out[i] is None or (v is not None and v>out[i]):
                out[i]=float(v) if v is not None else None
    return out

def model_M1(df):
    # ProSmooth
    z = 0.40*df["trend"] + 0.25*df["slope"]/(df["atr14"]+1e-12) + 0.20*(0.35-df["pctB"]) + 0.15*np.clip((df["bb_low"]-df["close"])/(df["atr14"]+1e-12),-2,2)
    raw = 100/(1+np.exp(-1.35*np.clip(z,-6,6)))
    return smooth(raw)

def model_M2(df):
    # AntiChase
    z = 0.45*df["trend"] + 0.30*df["slope"]/(df["atr14"]+1e-12) + 0.25*(0.35-df["pctB"])
    base = 100/(1+np.exp(-1.25*np.clip(z,-6,6)))
    over = np.clip((df["close"]-df["ema20"])/(df["atr14"]+1e-12),0,None)
    raw = base * (1/(1+0.8*over))
    return smooth(raw)

def model_M4(df):
    # SqueezeBreak
    kelt=1.5*df["atr14"]
    width=(df["bb_high"]-df["bb_low"])
    squeeze=(width/(kelt+1e-12)).rolling(5).mean()
    # low squeeze is good; expansion decay
    z = np.clip(1/(1+5*squeeze),0,1) + 0.3*np.clip(0.35-df["pctB"],-1,1)
    over = np.clip((df["close"]-df["ema20"])/(df["atr14"]+1e-12),0,None)
    raw = 100*z*(1/(1+0.9*over))
    return smooth(raw)

def model_M5(df):
    # ReboundLead
    rebound = np.clip((df["bb_low"]-df["close"])/(df["atr14"]+1e-12),0,2)
    z = 0.6*rebound + 0.25*np.clip(0.35-df["pctB"],-1,1) + 0.15*np.clip(df["trend"],-2,2)
    raw = 100/(1+np.exp(-2*np.clip(z-0.6,-6,6)))
    over = np.clip((df["close"]-df["ema20"])/(df["atr14"]+1e-12),0,None)
    raw = raw*(1/(1+0.6*over))
    return smooth(raw)

def build():
    labels=timeline(HOURS,15)
    end_iso=labels[-1]
    ex=getattr(ccxt,EXCHANGE_ID)({"enableRateLimit":True})
    out={"labels":labels, "model_names":{"M1":"ProSmooth","M2":"AntiChase","M4":"SqueezeBreak","M5":"ReboundLead"}, "symbols":{}}
    for sym in SYMBOLS:
        try:
            df=pd.DataFrame(ex.fetch_ohlcv(sym,"15m",limit=HOURS*4+120),
                            columns=["ts","open","high","low","close","volume"])
            df["ts"]=pd.to_datetime(df["ts"],unit="ms",utc=True)
            # patch live point at 'end_iso'
            live_dt=datetime.strptime(end_iso,"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            try:
                tkr=ex.fetch_ticker(sym); live=float(tkr.get("last") or tkr.get("close") or df["close"].iloc[-1])
            except Exception: live=float(df["close"].iloc[-1])
            if live_dt>df["ts"].max():
                df=pd.concat([df, pd.DataFrame([{"ts":live_dt,"open":live,"high":live,"low":live,"close":live,"volume":np.nan}])], ignore_index=True)
            else:
                df.loc[df["ts"]==live_dt, ["open","high","low","close"]]=live
            df=add_ind(df)
            # models
            m1=model_M1(df)
            m2=model_M2(df)
            m4=model_M4(df)
            m5=model_M5(df)
            # optional 5m lead -> aggregated
            try:
                df5=pd.DataFrame(ex.fetch_ohlcv(sym,"5m",limit=HOURS*12+90),
                                 columns=["ts","open","high","low","close","volume"])
                df5["ts"]=pd.to_datetime(df5["ts"],unit="ms",utc=True)
                df5=add_ind(df5)
                lead = model_M2(df5)  # fast anti-chase on 5m
                m3 = bin_to_15m(df5["ts"].tolist(), lead, labels)
            except Exception:
                m3=[None]*len(labels)
            # align 15m series
            close_map={t.strftime("%Y-%m-%dT%H:%M:%SZ"): float(v) for t,v in zip(df["ts"], df["close"])}
            def align(arr):
                arr_map={t.strftime("%Y-%m-%dT%H:%M:%SZ"): float(v) for t,v in zip(df["ts"], arr)}
                return [arr_map.get(lbl, None) for lbl in labels]
            price=[close_map.get(lbl,None) for lbl in labels]
            s1=align(m1); s2=align(m2); s4=align(m4); s5=align(m5)
            out["symbols"][sym]={
                "close": price,
                "score": s2,                      # legacy for old page
                "scores": {"M1":s1,"M2":s2,"M3":m3,"M4":s4,"M5":s5}
            }
        except Exception:
            out["symbols"][sym]={
                "close":[None]*len(labels),
                "score":[None]*len(labels),
                "scores":{"M1":[None]*len(labels),"M2":[None]*len(labels),
                          "M3":[None]*len(labels),"M4":[None]*len(labels),"M5":[None]*len(labels)}
            }
    DOCS.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out), encoding="utf-8")
    print("Wrote", OUT)

if __name__=="__main__":
    build()
