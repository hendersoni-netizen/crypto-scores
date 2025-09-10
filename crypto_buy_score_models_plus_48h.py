#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build 48h multi‑model buy scores and the web page JSON.
- Symbols: BTC/USDT, ETH/USDT, ONDO/USDT (Binance)
- Timeframe: 15m (48 hours)
- Models: M1..M10 (see MODEL_DESCRIPTIONS)
- Output: docs/data.json
- Also writes docs/index.html if an index file is not present.
Run from repo root:
    python3 crypto_buy_score_models_plus_48h.py
"""
import os, json, time, math
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

try:
    import ccxt
except Exception as e:
    raise SystemExit("Please `pip install ccxt pandas numpy` first") from e

SYMBOLS = ["BTC/USDT", "ETH/USDT", "ONDO/USDT"]
TIMEFRAME_MAIN = "15m"
TIMEFRAME_FAST = "5m"   # for M3 lead model
HOURS = 48
CSV_OUT = Path("docs") / "data.json"
HTML_OUT = Path("docs") / "index.html"

MODEL_DESCRIPTIONS = {
    "M1": "Baseline rule (EMA20>EMA50, touches lower BB, RSI<35, MACD turn)",
    "M2": "Z‑pullback: −zscore((EMA20−EMA50)/ATR) normalized",
    "M3": "5m lead: oversold composite collapsed to 15m",
    "M4": "MACD histogram upswing from negative (impulse turn)",
    "M5": "Squeeze start: rising BB width from low while below mid",
    "M6": "Pullback-in-uptrend: price near EMA20/50 lower band in EMA200 uptrend",
    "M7": "VWAP pullback (session anchored) in positive drift",
    "M8": "ROC(5) trough turning up (deceleration)",
    "M9": "StochRSI K up from oversold (trend-gated)",
    "M10":"Ensemble meta (emulates desired pre‑pump hump): 0.25*M2+0.25*M4+0.2*M6+0.2*M8+0.1*M3; EMA smooth",
}

def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def rsi(s, n=14):
    d = s.diff()
    up = (d.clip(lower=0)).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / (dn + 1e-12)
    return 100 - 100/(1+rs)

def macd(s, fast=12, slow=26, sig=9):
    ef = ema(s, fast)
    es = ema(s, slow)
    macd_line = ef - es
    signal = macd_line.ewm(span=sig, adjust=False).mean()
    hist = macd_line - signal
    return macd_line, signal, hist

def true_range(df):
    prev_close = df['close'].shift(1)
    tr = pd.concat([df['high']-df['low'], (df['high']-prev_close).abs(), (df['low']-prev_close).abs()], axis=1).max(axis=1)
    return tr

def atr(df, n=14):
    return true_range(df).rolling(n).mean()

def bollinger(s, n=20, k=2.0):
    mid = s.rolling(n).mean()
    sd  = s.rolling(n).std(ddof=0)
    up  = mid + k*sd
    lo  = mid - k*sd
    return lo, mid, up, (up-lo)  # width

def roc(s, n=5):
    return (s / s.shift(n) - 1.0) * 100.0

def stoch_rsi(s, n=14, k=3, d=3):
    r = rsi(s, n)
    minr = r.rolling(n).min()
    maxr = r.rolling(n).max()
    stoch = (r - minr) / ((maxr - minr) + 1e-12)
    kline = stoch.rolling(k).mean()
    dline = kline.rolling(d).mean()
    return kline, dline

def session_vwap(df):
    # Very light-weight daily anchored VWAP in UTC calendar days
    # typical price
    tp = (df['high'] + df['low'] + df['close'])/3
    date = df['ts'].dt.date
    cum_pv = (tp*df['volume']).groupby(date).cumsum()
    cum_v  = (df['volume']).groupby(date).cumsum()
    return (cum_pv / (cum_v + 1e-12))

def scale01(x, lo=None, hi=None):
    # robust scaling to 0..100
    x = pd.Series(x).astype(float)
    if lo is None: lo = x.quantile(0.05)
    if hi is None: hi = x.quantile(0.95)
    y = (x - lo) / (hi - lo + 1e-12)
    return (y.clip(0,1)*100.0)

def fetch_df(ex, sym, timeframe, hours):
    limit = int(hours*60/15) + 50 if timeframe=='15m' else int(hours*60/5) + 150
    o = ex.fetch_ohlcv(sym, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(o, columns=['ts','open','high','low','close','volume'])
    df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
    return df

def m1_baseline(df):
    lo, mid, up, width = bollinger(df['close'], 20, 2.0)
    ema20 = ema(df['close'],20)
    ema50 = ema(df['close'],50)
    r = rsi(df['close'],14)
    m, s, h = macd(df['close'])
    score = np.zeros(len(df))
    uptrend = ema20 > ema50
    touch = df['close'] <= lo
    macd_turn = (h.diff() > 0) & (h < 0)
    cond = uptrend & touch & (r < 35) & macd_turn
    score[cond] = 80
    score[cond & (r < 30)] = 90
    return pd.Series(score, index=df.index)

def m2_zpullback(df):
    a = atr(df, 14)
    z = (ema(df['close'],20) - ema(df['close'],50)) / (a + 1e-12)
    # we want high when z is negative (pullback), drop as it pumps
    return scale01(-z, lo=-2, hi=2)

def collapse_fast_to_main(df_fast, labels_main):
    # take last 3x5m per 15m bucket (align by <= bucket end)
    f = df_fast.copy()
    f.index = pd.DatetimeIndex(f['ts'])
    out = []
    for t in labels_main:
        # end of this 15m candle
        m = f.loc[:t].tail(3)
        out.append(m['lead'].mean() if len(m) else np.nan)
    return pd.Series(out, index=np.arange(len(labels_main)))

def m3_fast_lead(df_main, df_fast):
    # build 5m oversold composite then collapse to 15m
    f = df_fast.copy()
    r = rsi(f['close'], 7)
    k, d = stoch_rsi(f['close'], 14)
    pull = (f['close'] / ema(f['close'], 50) - 1.0) * -100
    lead = ( (1-k).fillna(0)*40 + (1-(r/100)).fillna(0)*40 + scale01(pull).fillna(0)*20 ) # 0..100-ish
    f['lead'] = lead.fillna(0)
    series = collapse_fast_to_main(f, list(df_main['ts']))
    return series.fillna(0).rolling(2, min_periods=1).mean().clip(0,100)

def m4_macd_turn(df):
    _, _, h = macd(df['close'])
    dh = h.diff()
    sig = (-dh).clip(lower=0)  # rising hist from negative -> positive slope
    sig = sig.where(h < 0, 0)  # only when below zero
    return scale01(sig)

def m5_squeeze(df):
    lo, mid, up, width = bollinger(df['close'], 20, 2.0)
    bw = width / mid
    slope = bw.diff()
    # High when bandwidth starts rising from a low level and price still below mid
    raw = (scale01(-bw, lo=None, hi=None) * 0.4 + scale01(slope, lo=None, hi=None)*0.6) * (df['close'] < mid).astype(float)
    return raw.clip(0,100).rolling(2, min_periods=1).mean()

def m6_pullback_uptrend(df):
    ema20v = ema(df['close'],20); ema50v = ema(df['close'],50); ema200v = ema(df['close'],200)
    uptrend = (ema50v > ema200v).astype(float)
    dist = (df['close'] - ema20v).abs() + (df['close'] - ema50v).abs()
    raw = scale01(-dist) * uptrend
    return raw.clip(0,100)

def m7_vwap_pull(df):
    v = session_vwap(df)
    drift = ema(df['close'], 100).diff().clip(lower=0)
    raw = scale01((v - df['close'])/df['close']) * scale01(drift)
    return raw.clip(0,100).rolling(3, min_periods=1).mean()

def m8_roc_trough(df):
    r = roc(df['close'], 5)
    dr = r.diff()
    raw = (-r).clip(lower=0) * (dr > 0).astype(float)
    return scale01(raw).rolling(2, min_periods=1).mean()

def m9_stoch_rsi(df):
    k, d = stoch_rsi(df['close'], 14)
    trend = (ema(df['close'], 50) > ema(df['close'], 200)).astype(float)
    raw = (1 - k) * 100 * trend
    return raw.rolling(3, min_periods=1).mean().clip(0,100)

def m10_meta(df, m2, m3, m4, m6, m8):
    meta = 0.25*m2 + 0.10*m3 + 0.25*m4 + 0.20*m6 + 0.20*m8
    return ema(meta, 3).clip(0,100)

def build():
    ex = ccxt.binance({"enableRateLimit": True})
    out = {
        "labels": [],
        "symbols": {},
        "models": {},   # models[symbol][Mx] = list
        "meta": {
            "updated_utc": datetime.now(timezone.utc).isoformat(),
            "hours": HOURS,
            "timeframe": TIMEFRAME_MAIN,
            "model_descriptions": MODEL_DESCRIPTIONS,
        },
    }
    # Fetch main once to set labels
    for sym in SYMBOLS:
        df = fetch_df(ex, sym, TIMEFRAME_MAIN, HOURS)
        out["symbols"][sym] = {"close": list(map(float, df['close'].tolist()))}
        if not out["labels"]:
            out["labels"] = [ts.isoformat() for ts in df['ts']]
        # indicators on main
        df_main = df.copy()
        # fast df for M3
        df_fast = fetch_df(ex, sym, TIMEFRAME_FAST, HOURS)
        # compute models
        m1 = m1_baseline(df_main)
        m2 = m2_zpullback(df_main)
        m4s = m4_macd_turn(df_main)
        m5s = m5_squeeze(df_main)
        m6s = m6_pullback_uptrend(df_main)
        m7s = m7_vwap_pull(df_main)
        m8s = m8_roc_trough(df_main)
        m9s = m9_stoch_rsi(df_main)
        m3s = m3_fast_lead(df_main, df_fast)
        m10s = m10_meta(df_main, m2, m3s, m4s, m6s, m8s)
        out["models"][sym] = {
            "M1": list(map(float, m1.fillna(0).tolist())),
            "M2": list(map(float, m2.fillna(0).tolist())),
            "M3": list(map(float, m3s.fillna(0).tolist())),
            "M4": list(map(float, m4s.fillna(0).tolist())),
            "M5": list(map(float, m5s.fillna(0).tolist())),
            "M6": list(map(float, m6s.fillna(0).tolist())),
            "M7": list(map(float, m7s.fillna(0).tolist())),
            "M8": list(map(float, m8s.fillna(0).tolist())),
            "M9": list(map(float, m9s.fillna(0).tolist())),
            "M10": list(map(float, m10s.fillna(0).tolist())),
        }
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(CSV_OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    # If HTML missing, write a minimal page (your repo may already have one)
    if not HTML_OUT.exists():
        HTML_OUT.write_text(BASIC_HTML, encoding="utf-8")
    print("Wrote", CSV_OUT, "and updated", HTML_OUT)

BASIC_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Price & Strong Buy score (last 48 hours)</title>
  <style>
    body{font-family:-apple-system,Helvetica,Arial,sans-serif;margin:20px;}
    .container{max-width:1100px;margin:0 auto;}
    .chart-card{margin:18px 0;padding:12px 12px 6px;border:1px solid #eee;border-radius:12px;box-shadow:0 1px 2px rgba(0,0,0,.04);}
    .header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:6px 0 2px}
    .meta{color:#666;font-size:12px}
    .toggles label{margin-right:10px;font-size:12px;user-select:none}
    canvas{width:100%;height:340px}
  </style>
</head>
<body>
<div class="container">
  <h3>Price & Strong Buy score (last 48 hours)</h3>
  <div class="meta">Left axis: price. Right axis: score (0–100). Hourly labels; 15‑minute ticks.
    <br><span id="lastUpdated"></span>
  </div>
  <div id="charts"></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script>
(async () => {
  const res = await fetch('data.json?t=' + Date.now(), {cache:'no-store'});
  const data = await res.json();
  document.getElementById('lastUpdated').textContent =
    'Last updated (UTC): ' + new Date(data.meta.updated_utc).toISOString();

  const labels = data.labels.map(t => new Date(t));
  const palette = ['#1f77b4','#d62728','#2ca02c','#9467bd','#8c564b','#e377c2','#7f7f7f','#bcbd22','#17becf','#ff7f0e'];

  function makeCard(sym) {
    const wrap = document.createElement('div');
    wrap.className = 'chart-card';
    const head = document.createElement('div');
    head.className = 'header';
    head.innerHTML = `<b>${sym}</b> <span class="toggles"></span>`;
    const toggles = head.querySelector('.toggles');
    const c = document.createElement('canvas'); wrap.append(head,c);
    document.getElementById('charts').appendChild(wrap);
    const ctx = c.getContext('2d');

    const price = data.symbols[sym].close;
    const models = data.models[sym];

    // default: show M10 only
    const enabled = {M10:true};
    Object.keys(models).forEach(k => { if(!(k in enabled)) enabled[k]=false });

    // build checkboxes
    Object.keys(models).sort().forEach((k,i) => {
      const id = sym.replace(/[^A-Za-z0-9]/g,'') + '_' + k;
      const cb = document.createElement('input'); cb.type='checkbox'; cb.id=id; cb.checked=enabled[k];
      const lab = document.createElement('label'); lab.htmlFor=id; lab.style.color = palette[i%palette.length];
      lab.textContent = ' ' + k + ' ';
      toggles.append(cb, lab);
    });

    // Now line plugin
    const lastLabel = labels[labels.length-1];
    const nowLine = {
      id:'nowline',
      afterDatasetsDraw(chart, args, opts){
        const {ctx, chartArea:{left, right, top, bottom}, scales:{x}} = chart;
        ctx.save(); ctx.strokeStyle='#111'; ctx.setLineDash([2,2]); ctx.lineWidth=1.2;
        const xNow = x.getPixelForValue(lastLabel);
        ctx.beginPath(); ctx.moveTo(xNow, top); ctx.lineTo(xNow, bottom); ctx.stroke(); ctx.restore();
      }
    };

    const chart = new Chart(ctx, {
      type: 'line',
      data: {labels, datasets:[
        {label: sym+' price', yAxisID:'price', data: price, borderColor:'#5585c2', tension:.25, spanGaps:true, pointRadius:0},
      ]},
      options: {
        responsive:true, interaction:{mode:'nearest', intersect:false},
        scales: {
          x: { type:'time', time:{tooltipFormat:'HH:mm', displayFormats:{minute:'HH:mm'}}, ticks:{autoSkip:true, maxTicksLimit:49}},
          price: { position:'left', grid:{color:'rgba(0,0,0,.06)'}},
          score: { position:'right', min:0, max:100, ticks:{stepSize:10}, grid:{drawOnChartArea:false} }
        },
        plugins:{ legend:{position:'top'} }
      },
      plugins:[nowLine]
    });

    function rebuild(){
      const ds = [ chart.data.datasets[0] ];
      let i=0;
      Object.keys(models).sort().forEach(k=>{
        const cb = toggles.querySelector(`#${sym.replace(/[^A-Za-z0-9]/g,'')}_${k}`);
        if(cb && cb.checked){
          ds.push({label:k, yAxisID:'score', data: models[k], tension:.25, spanGaps:true, pointRadius:0,
                   borderColor: palette[i%palette.length] });
        }
        i++;
      });
      chart.data.datasets = ds;
      chart.update('none');
    }

    toggles.querySelectorAll('input[type=checkbox]').forEach(cb=>cb.addEventListener('change', rebuild));
    rebuild();
  }

  Object.keys(data.symbols).forEach(makeCard);
})().catch(e=>console.error(e));
</script>
</body>
</html>
"""

if __name__ == "__main__":
    build()
