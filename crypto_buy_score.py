#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
crypto_buy_score.py
- Fetches 15m OHLCV (Binance via ccxt)
- Calculates indicators & a simple buy score
- Appends one row per symbol to docs/buy_scores.csv
- Rebuilds docs/index.html with a 12-hour chart (15-min buckets)
- Commits & pushes docs/ to GitHub Pages
"""

import time
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

# =========================
# CONFIG
# =========================
EXCHANGE_ID = "binance"
SYMBOLS = ["BTC/USDT", "ETH/USDT", "ONDO/USDT"]

TIMEFRAME = "15m"
LOOKBACK_HOURS = 12            # show last 12h on the chart
UI_INTERVAL_MINUTES = 15       # shown on the page (countdown), not the scheduler

CSV_PATH = Path("docs/buy_scores.csv")
HTML_PATH = Path("docs/index.html")

W_EMA = 25
W_BB  = 25
W_RSI = 25
W_MACD= 25

exchange = getattr(ccxt, EXCHANGE_ID)({"enableRateLimit": True})

# -------------------------
# Timestamp helpers (strict ISO 'Z')
# -------------------------
def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def iso_utc_now() -> str:
    return iso_utc(datetime.now(timezone.utc))

# -------------------------
# Indicators
# -------------------------
def ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, adjust=False).mean()

def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    g = np.where(d > 0, d, 0.0)
    l = np.where(d < 0, -d, 0.0)
    g = pd.Series(g, index=s.index).ewm(alpha=1/n, adjust=False).mean()
    l = pd.Series(l, index=s.index).ewm(alpha=1/n, adjust=False).mean()
    rs = g/(l+1e-12)
    return 100 - (100/(1+rs))

def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    mf, ms = ema(s, fast), ema(s, slow)
    m = mf - ms
    sig = m.ewm(span=signal, adjust=False).mean()
    return m, sig, m - sig

def bollinger(s: pd.Series, n: int = 20, k: float = 2.0):
    mid = s.rolling(n).mean()
    std = s.rolling(n).std(ddof=0)
    return mid - k*std, mid, mid + k*std

# -------------------------
# Data + score
# -------------------------
def fetch_df(sym: str, timeframe: str, hours: int) -> pd.DataFrame:
    limit = int(hours*60/15) + 50
    o = exchange.fetch_ohlcv(sym, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(o, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    df["ema20"] = ema(c, 20)
    df["ema50"] = ema(c, 50)
    df["bb_low"], df["bb_mid"], df["bb_high"] = bollinger(c, 20, 2.0)
    df["rsi"] = rsi(c, 14)
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(c, 12, 26, 9)
    return df.dropna().copy()

def score_row(r: pd.Series) -> int:
    s = 0
    if r["ema20"] > r["ema50"]: s += W_EMA
    if r["close"] <= r["bb_low"]: s += W_BB
    if r["rsi"] < 35: s += W_RSI
    if r["macd"] > r["macd_signal"] and r["macd_hist"] > 0: s += W_MACD
    return int(s)

# -------------------------
# Output
# -------------------------
def log_to_csv(records: list[dict]):
    if not records: return
    df = pd.DataFrame.from_records(records)
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    header = not CSV_PATH.exists()
    df.to_csv(CSV_PATH, mode="a", index=False, header=header)

def _last_run_iso() -> str:
    try:
        if CSV_PATH.exists():
            df = pd.read_csv(CSV_PATH, usecols=["timestamp_utc"])
            if not df.empty:
                t = pd.to_datetime(df["timestamp_utc"], utc=True).max().to_pydatetime()
                return iso_utc(t)
    except Exception:
        pass
    return iso_utc_now()

def write_html_from_csv():
    # small pre-rendered table
    initial_table = ""
    try:
        if CSV_PATH.exists():
            df = pd.read_csv(CSV_PATH)
            if not df.empty:
                latest = (df.sort_values("timestamp_utc", ascending=False)
                            .groupby("symbol", as_index=False).first())
                initial_table = latest.to_html(index=False, float_format=lambda x: f"{x:.6f}")
    except Exception:
        pass

    last_run_iso = _last_run_iso()

    html = """<!doctype html><html><head><meta charset="utf-8"><title>Buy Scores</title>
<style>
body{font-family:-apple-system,Helvetica,Arial,sans-serif;margin:20px}
h2{margin:0 0 8px}
.status{display:flex;gap:16px;align-items:center;margin:8px 0 14px;flex-wrap:wrap}
.dot{width:10px;height:10px;border-radius:50%;background:#ef4444;box-shadow:0 0 6px #ef4444}
.kv span{font-weight:600}
table{border-collapse:collapse;width:100%;margin-top:16px}
th,td{border:1px solid #ddd;padding:6px;text-align:right}
th{text-align:center;background:#f5f5f5}
.container{max-width:1100px;margin:0 auto}
canvas{width:100%;height:420px}
.note{color:#666;font-size:12px;margin-top:6px}
</style></head><body>
<div class="container">
  <h2>Buy Scores (latest per symbol)</h2>
  <div class="status">
    <div id="statusDot" class="dot"></div>
    <div class="kv">Last run (UTC): <span id="lastRun">__LAST_RUN__</span></div>
    <div class="kv"><small>Uptime since last run:</small> <span id="uptime">—</span></div>
    <div class="kv"><small>Next run in (~every __UI_INTERVAL__ min):</small> <span id="countdown">—</span></div>
  </div>
  <div style="margin:12px 0">
    <canvas id="scoreChart"></canvas>
    <div class="note">X axis: left = __LOOKBACK__ hours ago, right = now.</div>
  </div>
  <div id="tableWrap">__TABLE__</div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3"></script>
<script>
const LOOKBACK_HOURS = __LOOKBACK__;
const UI_INTERVAL_MINUTES = __UI_INTERVAL__;
const FRESH_MS = 10*60*1000;

// elements
const statusDot=document.getElementById('statusDot');
const lastRunEl=document.getElementById('lastRun');
const uptimeEl=document.getElementById('uptime');
const countdownEl=document.getElementById('countdown');

// chart
const ctx=document.getElementById('scoreChart').getContext('2d');
const chart=new Chart(ctx,{type:'line',data:{labels:[],datasets:[]},
  options:{responsive:true,interaction:{mode:'nearest',intersect:false},
    plugins:{legend:{position:'top'},title:{display:true,text:'Strong Buy Score (0–100) — last '+LOOKBACK_HOURS+' hours'}},
    scales:{x:{type:'time',time:{tooltipFormat:'HH:mm',displayFormats:{minute:'HH:mm'}},ticks:{autoSkip:true,maxTicksLimit:25}},
            y:{min:0,max:100,ticks:{stepSize:20}}}});

// helpers
const log=(...a)=>{ try{console.log(...a);}catch(_){} };

function parseCSV(t){
  const L=t.trim().split(/\\r?\\n/); if(!L.length) return [];
  const H=L.shift().split(',');
  return L.map(line=>{
    const C=line.split(',');
    const o={}; H.forEach((h,i)=>o[h]=C[i]);
    return o;
  });
}
function fmt(ms){
  if(!Number.isFinite(ms)) return '—';
  if(ms<0) ms=0;
  const s=Math.floor(ms/1000);
  const h=String(Math.floor(s/3600)).padStart(2,'0');
  const m=String(Math.floor((s%3600)/60)).padStart(2,'0');
  const x=String(s%60).padStart(2,'0');
  return `${h}:${m}:${x}`;
}
function floor15(d){ const S=900000; return new Date(Math.floor(d.getTime()/S)*S); }
function nextCountdown(){ const S=900000; const n=new Date(Math.ceil(Date.now()/S)*S); return n-Date.now(); }

function buildTimeline(lastMs,hours){
  const end=new Date(lastMs); const start=new Date(end.getTime()-hours*3600000);
  const out=[]; const step=900000; // 15m
  for(let t=floor15(start); t<=floor15(end); t=new Date(t.getTime()+step)) out.push(new Date(t).toISOString());
  return out;
}

function seriesFromRows(rows,timeline){
  const bySym={}; const syms=[...new Set(rows.map(r=>r.symbol))];
  syms.forEach(s=>bySym[s]=new Array(timeline.length).fill(null));
  const idx=new Map(timeline.map((t,i)=>[t,i]));
  rows.sort((a,b)=>Date.parse(a.timestamp_utc)-Date.parse(b.timestamp_utc));
  rows.forEach(r=>{
    const tsMs=Date.parse(r.timestamp_utc);
    if(!Number.isFinite(tsMs)) return;
    const bucketIso=floor15(new Date(tsMs)).toISOString();
    const i=idx.get(bucketIso); if(i===undefined) return;
    const v=parseInt(r.score,10); if(Number.isFinite(v)) bySym[r.symbol][i]=v;
  });
  return bySym;
}

async function loadAndUpdate(){
  try{
    const res=await fetch('buy_scores.csv?t='+Date.now(),{cache:'no-store'});
    if(!res.ok) throw new Error('HTTP '+res.status);
    const text=await res.text();
    const rows=parseCSV(text);
    if(!rows.length) return log('No rows');

    // robust "last run"
    const times=rows.map(r=>Date.parse(r.timestamp_utc)).filter(Number.isFinite);
    if(!times.length) throw new Error('No parseable timestamps');
    const lastMs=Math.max(...times);
    const lastIso=new Date(lastMs).toISOString();
    lastRunEl.textContent=lastIso;

    // status + timers
    const ms=Date.now()-lastMs;
    const fresh=ms<FRESH_MS;
    statusDot.style.background=fresh?'#22c55e':'#ef4444';
    statusDot.style.boxShadow=fresh?'0 0 6px #22c55e':'0 0 6px #ef4444';
    uptimeEl.textContent=fmt(ms);
    countdownEl.textContent=fmt(nextCountdown());

    // table (latest per symbol)
    const latest=new Map();
    rows.slice().sort((a,b)=>Date.parse(b.timestamp_utc)-Date.parse(a.timestamp_utc))
        .forEach(r=>{ if(!latest.has(r.symbol)) latest.set(r.symbol,r); });
    (function renderTable(){
      const cols=["timestamp_utc","symbol","score","close","ema20","ema50","rsi","macd","macd_signal","macd_hist","bb_low","bb_mid","bb_high"];
      let h='<table><thead><tr>'+cols.map(c=>'<th>'+c+'</th>').join('')+'</tr></thead><tbody>';
      Array.from(latest.values()).forEach(r=>{ h+='<tr>'+cols.map(c=>'<td>'+(r[c]??'')+'</td>').join('')+'</tr>'; });
      h+='</tbody></table>'; document.getElementById('tableWrap').innerHTML=h;
    })();

    // chart series
    const tl=buildTimeline(lastMs, LOOKBACK_HOURS);
    const series=seriesFromRows(rows, tl);
    chart.data.labels=tl;

    const palette=["#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd","#8c564b","#e377c2","#7f7f7f","#bcbd22","#17becf"];
    const syms=Object.keys(series);
    chart.data.datasets=syms.map((s,i)=>({
      label:s,
      data:series[s],
      borderColor:palette[i%palette.length],
      backgroundColor:palette[i%palette.length],
      spanGaps:true,
      tension:.25,
      pointRadius:3,
      pointHoverRadius:5
    }));
    chart.update('none');
    log('Updated chart. labels:', tl.length, 'datasets:', syms);
  }catch(e){
    console.error('loadAndUpdate error:', e);
  }
}

loadAndUpdate();
setInterval(()=>{ const s=Date.parse(lastRunEl.textContent.trim()); if(Number.isFinite(s)){ const ms=Date.now()-s; uptimeEl.textContent=fmt(ms); countdownEl.textContent=fmt(nextCountdown()); }}, 1000);
setInterval(loadAndUpdate, 30000);
</script></body></html>"""

    html = (html
            .replace("__LAST_RUN__", last_run_iso)
            .replace("__LOOKBACK__", str(LOOKBACK_HOURS))
            .replace("__UI_INTERVAL__", str(UI_INTERVAL_MINUTES))
            .replace("__TABLE__", initial_table))
    HTML_PATH.write_text(html, encoding="utf-8")

def git_push():
    try:
        subprocess.run(["git","add","docs"], check=True)
        subprocess.run(["git","commit","-m", f"Update {iso_utc_now()}"], check=True)
        subprocess.run(["git","push"], check=True)
        print("✅ Pushed to GitHub.")
    except Exception as e:
        print("⚠️ Git push failed:", e)

def run_once():
    now = iso_utc_now()
    rows: list[dict] = []
    for sym in SYMBOLS:
        try:
            df = fetch_df(sym, TIMEFRAME, LOOKBACK_HOURS)
            df = add_indicators(df)
            last = df.iloc[-1]
            score = score_row(last)
            print(f"{sym}: {score}/100 | Close={last['close']:.2f} | RSI={last['rsi']:.1f}")
            rows.append({
                "timestamp_utc": now,
                "symbol": sym,
                "score": int(score),
                "close": float(last["close"]),
                "ema20": float(last["ema20"]),
                "ema50": float(last["ema50"]),
                "rsi": float(last["rsi"]),
                "macd": float(last["macd"]),
                "macd_signal": float(last["macd_signal"]),
                "macd_hist": float(last["macd_hist"]),
                "bb_low": float(last["bb_low"]),
                "bb_mid": float(last["bb_mid"]),
                "bb_high": float(last["bb_high"]),
            })
            time.sleep(0.2)
        except Exception:
            import traceback; print(f"{sym}: ERROR"); traceback.print_exc()

    if rows:
        log_to_csv(rows)
        write_html_from_csv()
        git_push()
    else:
        print("No records written; skipping push.")

if __name__ == "__main__":
    run_once()
