#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time, os, subprocess
from datetime import datetime, timezone
import ccxt
import pandas as pd
import numpy as np
from pathlib import Path

EXCHANGE_ID = "binance"
SYMBOLS = ["BTC/USDT", "ETH/USDT", "ONDO/USDT"]
TIMEFRAME = "15m"
LOOKBACK_HOURS = 6

RUN_CONTINUOUS = False          # launchd runs every 5 minutes
INTERVAL_MINUTES = 5

CSV_PATH = Path("docs/buy_scores.csv")
HTML_PATH = Path("docs/index.html")

W_EMA=W_BB=W_RSI=W_MACD=25

exchange = getattr(ccxt, EXCHANGE_ID)({"enableRateLimit": True})

def ema(s, p): return s.ewm(span=p, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    g = np.where(d>0,d,0.0); l = np.where(d<0,-d,0.0)
    g = pd.Series(g, index=s.index).ewm(alpha=1/n, adjust=False).mean()
    l = pd.Series(l, index=s.index).ewm(alpha=1/n, adjust=False).mean()
    rs = g/(l+1e-12); return 100-(100/(1+rs))

def macd(s,f=12,sl=26,sg=9):
    mf, ms = ema(s,f), ema(s,sl)
    m = mf-ms; sig = m.ewm(span=sg, adjust=False).mean()
    return m, sig, m-sig

def bollinger(s, n=20, k=2.0):
    mid = s.rolling(n).mean(); std = s.rolling(n).std(ddof=0)
    return mid-k*std, mid, mid+k*std

def fetch_df(sym, tf, hrs):
    limit = int(hrs*60/15)+50
    o = exchange.fetch_ohlcv(sym, timeframe=tf, limit=limit)
    df = pd.DataFrame(o, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df

def add_indicators(df):
    c=df["close"]
    df["ema20"]=ema(c,20); df["ema50"]=ema(c,50)
    df["bb_low"],df["bb_mid"],df["bb_high"]=bollinger(c,20,2.0)
    df["rsi"]=rsi(c,14)
    df["macd"],df["macd_signal"],df["macd_hist"]=macd(c,12,26,9)
    return df.dropna().copy()

def score_row(r):
    s=0
    if r["ema20"]>r["ema50"]: s+=W_EMA
    if r["close"]<=r["bb_low"]: s+=W_BB
    if r["rsi"]<35: s+=W_RSI
    if r["macd"]>r["macd_signal"] and r["macd_hist"]>0: s+=W_MACD
    return int(s)

def log_to_csv(rows):
    if not rows: return
    df=pd.DataFrame.from_records(rows)
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    hdr=not CSV_PATH.exists()
    df.to_csv(CSV_PATH, mode="a", index=False, header=hdr)

def _last_run_iso():
    if CSV_PATH.exists():
        try:
            df=pd.read_csv(CSV_PATH, usecols=["timestamp_utc"])
            if not df.empty:
                t=pd.to_datetime(df["timestamp_utc"]).max().to_pydatetime().replace(tzinfo=timezone.utc)
                return t.isoformat()
        except Exception: pass
    return datetime.now(timezone.utc).isoformat()

def write_html_from_csv():
    initial_table=""
    if CSV_PATH.exists():
        try:
            df=pd.read_csv(CSV_PATH)
            if not df.empty:
                latest=(df.sort_values("timestamp_utc", ascending=False)
                          .groupby("symbol", as_index=False).first())
                initial_table=latest.to_html(index=False, float_format=lambda x:f"{x:.4f}")
        except Exception: pass

    last_run_iso=_last_run_iso()

    html=f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Buy Scores</title>
<style>
body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 20px; }}
h2 {{ margin: 0 0 8px; }}
.status {{ display: flex; gap: 16px; align-items: center; margin: 8px 0 14px; flex-wrap: wrap; }}
.dot {{ width: 10px; height: 10px; border-radius: 50%; background: #22c55e; box-shadow: 0 0 6px #22c55e; transition: background .2s, box-shadow .2s; }}
.kv span {{ color: #111; font-weight: 600; }}
.kv small {{ color: #666; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
th, td {{ border: 1px solid #ddd; padding: 6px; text-align: right; }}
th {{ background: #f5f5f5; text-align: center; }}
.container {{ max-width: 1100px; margin: 0 auto; }}
canvas {{ width: 100%; height: 420px; }}
.note {{ color: #666; font-size: 12px; margin-top: 6px; }}
</style>
</head>
<body>
<div class="container">
  <h2>Buy Scores (latest per symbol)</h2>
  <div class="status">
    <div id="statusDot" class="dot" title="Status"></div>
    <div class="kv">Last run (UTC): <span id="lastRun">{last_run_iso}</span></div>
    <div class="kv"><small>Uptime since last run:</small> <span id="uptime">—</span></div>
    <div class="kv"><small>Next run in (~every {INTERVAL_MINUTES} min):</small> <span id="countdown">—</span></div>
  </div>

  <div style="margin: 12px 0;">
    <canvas id="scoreChart"></canvas>
    <div class="note">X axis shows time reversed: left = {LOOKBACK_HOURS} hours ago, right = now.</div>
  </div>

  <div id="tableWrap">
    {initial_table}
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3"></script>
<script>
const INTERVAL_MINUTES={INTERVAL_MINUTES};
const LOOKBACK_HOURS={LOOKBACK_HOURS};
const FRESH_THRESHOLD_MS=10*60*1000;

const statusDot=document.getElementById('statusDot');
const lastRunEl=document.getElementById('lastRun');
const uptimeEl=document.getElementById('uptime');
const countdownEl=document.getElementById('countdown');

const ctx=document.getElementById('scoreChart').getContext('2d');
const chart=new Chart(ctx,{{
  type:'line',
  data:{{labels:[],datasets:[]}},
  options:{{
    responsive:true,
    interaction:{{mode:'nearest',intersect:false}},
    plugins:{{legend:{{position:'top'}},title:{{display:true,text:'Strong Buy Score (0–100) — last '+LOOKBACK_HOURS+' hours'}}}},
    scales:{{x:{{type:'time',reverse:true,time:{{tooltipFormat:'HH:mm',displayFormats:{{minute:'HH:mm'}}}},ticks:{{autoSkip:true,maxTicksLimit:13}}}},
            y:{{min:0,max:100,ticks:{{stepSize:20}}}}}}
}});

function parseCSV(text){{
  const lines=text.trim().split(/\\r?\\n/);
  const headers=lines.shift().split(',');
  return lines.map(line=>{{const cols=line.split(',');const o={{}};headers.forEach((h,i)=>o[h]=cols[i]);return o;}});
}}
function fmt(ms){{if(ms<0)ms=0;const s=Math.floor(ms/1000);const hh=String(Math.floor(s/3600)).padStart(2,'0');const mm=String(Math.floor((s%3600)/60)).padStart(2,'0');const ss=String(Math.floor(s%60)).padStart(2,'0');return `${{hh}}:${{mm}}:${{ss}}`;}}
function nextCountdown(now=new Date()){{const m=INTERVAL_MINUTES*60*1000;const next=new Date(Math.ceil(now.getTime()/m)*m);return next-now;}}

function renderTable(rows){{
  const cols=["timestamp_utc","symbol","score","close","ema20","ema50","rsi","macd","macd_signal","macd_hist","bb_low","bb_mid","bb_high"];
  let html='<table><thead><tr>'+cols.map(c=>'<th>'+c+'</th>').join('')+'</tr></thead><tbody>';
  rows.forEach(r=>{{html+='<tr>'+cols.map(c=>'<td>'+(r[c]??'')+'</td>').join('')+'</tr>';}});html+='</tbody></table>';
  document.getElementById('tableWrap').innerHTML=html;
}}

function buildTimeline(endUtc,hours){{
  const end=new Date(endUtc);const start=new Date(end.getTime()-hours*60*60*1000);
  const out=[];const step=15*60*1000;const alignedEnd=new Date(Math.floor(end.getTime()/step)*step);
  for(let t=new Date(Math.floor(start.getTime()/step)*step);t<=alignedEnd;t=new Date(t.getTime()+step)) out.push(new Date(t).toISOString());
  return out;
}}
function seriesFromRows(rows,timeline){{
  const bySym={{}};const syms=new Set(rows.map(r=>r.symbol));syms.forEach(sym=>bySym[sym]=new Array(timeline.length).fill(null));
  const idx=new Map(timeline.map((t,i)=>[t,i]));
  rows.forEach(r=>{{const t=new Date(r.timestamp_utc).toISOString();const i=idx.get(t);if(i!==undefined){{const v=parseInt(r.score,10);if(!Number.isNaN(v)) bySym[r.symbol][i]=v;}}}});
  return bySym;
}}

async function loadAndUpdate(){{
  try{{
    const res=await fetch('buy_scores.csv?t='+Date.now(),{{cache:'no-store'}});
    if(!res.ok) throw new Error('fetch failed: '+res.status);
    const text=await res.text(); const rows=parseCSV(text); if(!rows.length) return;

    const last=rows.reduce((a,r)=>a>r.timestamp_utc?a:r.timestamp_utc,rows[0].timestamp_utc);
    lastRunEl.textContent=last;
    const msSince=Date.now()-new Date(last).getTime();
    const fresh=msSince<FRESH_THRESHOLD_MS;
    statusDot.style.background=fresh?'#22c55e':'#ef4444';
    statusDot.style.boxShadow=fresh?'0 0 6px #22c55e':'0 0 6px #ef4444';
    uptimeEl.textContent=fmt(msSince);
    countdownEl.textContent=fmt(nextCountdown());

    const latestBySym=new Map();
    rows.slice().sort((a,b)=>new Date(b.timestamp_utc)-new Date(a.timestamp_utc))
        .forEach(r=>{{if(!latestBySym.has(r.symbol)) latestBySym.set(r.symbol,r);}});
    renderTable(Array.from(latestBySym.values()));

    const timeline=buildTimeline(last, {LOOKBACK_HOURS});
    const series=seriesFromRows(rows,timeline);
    chart.data.labels=timeline;
    const palette=["#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd","#8c564b","#e377c2","#7f7f7f","#bcbd22","#17becf"];
    const syms=Object.keys(series);
    chart.data.datasets=syms.map((sym,i)=>({{
      label: sym,
      data: series[sym],
      borderColor: palette[i%palette.length],
      backgroundColor: palette[i%palette.length],
      spanGaps: true, tension: .25, pointRadius: 0
    }}));
    chart.update('none');
  }}catch(e){{console.error(e);}}
}}

loadAndUpdate();
setInterval(()=>{{const t=new Date(lastRunEl.textContent.trim());if(!isNaN(t)){{const ms=Date.now()-t.getTime();uptimeEl.textContent=fmt(ms);countdownEl.textContent=fmt(nextCountdown());}}}},1000);
setInterval(loadAndUpdate,30000);
</script>
</body>
</html>"""
    HTML_PATH.write_text(html, encoding="utf-8")

def git_push():
    try:
        subprocess.run(["git", "add", "docs"], check=True)
        subprocess.run(["git", "commit", "-m", f"Update {datetime.now().isoformat()}"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("✅ Pushed to GitHub.")
    except Exception as e:
        print("⚠️ Git push failed:", e)

def run_once():
    now=datetime.now(timezone.utc).isoformat()
    rows=[]
    for sym in SYMBOLS:
        try:
            df=fetch_df(sym,TIMEFRAME,LOOKBACK_HOURS)
            df=add_indicators(df); last=df.iloc[-1]; score=score_row(last)
            print(f"{sym}: {score}/100 | Close={last['close']:.2f} | RSI={last['rsi']:.1f}")
            rows.append({{
                "timestamp_utc":now, "symbol":sym, "score":score,
                "close":float(last["close"]), "ema20":float(last["ema20"]), "ema50":float(last["ema50"]),
                "rsi":float(last["rsi"]), "macd":float(last["macd"]), "macd_signal":float(last["macd_signal"]),
                "macd_hist":float(last["macd_hist"]), "bb_low":float(last["bb_low"]),
                "bb_mid":float(last["bb_mid"]), "bb_high":float(last["bb_high"])
            }})
            time.sleep(0.25)
        except Exception as e:
            print(f"{sym}: ERROR {e}")
    log_to_csv(rows); write_html_from_csv(); git_push()

def seconds_until_next_interval(m):
    now=time.time(); interval=m*60
    return int(interval-(now%interval)) or interval

if __name__=="__main__":
    if RUN_CONTINUOUS:
        while True:
            print(f"=== Run {datetime.now(timezone.utc).isoformat()} ===")
            run_once(); wait=seconds_until_next_interval(INTERVAL_MINUTES)
            print(f"Sleeping {wait}s..."); time.sleep(wait)
    else:
        run_once()
