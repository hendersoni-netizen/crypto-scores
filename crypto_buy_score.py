#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ---- BTC price + Strong Buy score (right y-axis 0-100) ----
# - Fetches last 12h of BTC/USDT 15m candles (via ccxt)
# - Computes indicators + a simple Strong Buy score per bar
# - Saves docs/btc.json with {t, close, score}
# - Writes a tiny docs/index.html plotting price (left) + score (right)
# - Commits and pushes

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

# ---------- Settings ----------
EXCHANGE   = ccxt.binance({'enableRateLimit': True})
SYMBOL     = 'BTC/USDT'
TIMEFRAME  = '15m'
HOURS      = 12
POINTS     = HOURS * 60 // 15  # 48 points (12h of 15m bars)

ROOT       = Path('.')
DOCS       = ROOT / 'docs'
JSON_PATH  = DOCS / 'btc.json'
HTML_PATH  = DOCS / 'index.html'

# Weights for score (max 100)
W_EMA = 25
W_BB  = 25
W_RSI = 25
W_MACD= 25


def iso_utc(ts_ms: int) -> str:
    """ms -> 'YYYY-MM-DDTHH:MM:SSZ'"""
    return datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def fetch_btc_df():
    # cushion extra bars for indicators
    limit = POINTS + 100
    ohlcv = EXCHANGE.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
    df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
    return df


# --------- Indicators ---------
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    gain = pd.Series(gain, index=series.index).ewm(alpha=1/length, adjust=False).mean()
    loss = pd.Series(loss, index=series.index).ewm(alpha=1/length, adjust=False).mean()
    rs = gain / (loss + 1e-12)
    return 100 - (100 / (1 + rs))

def macd(series: pd.Series, fast=12, slow=26, signal=9):
    mf = ema(series, fast)
    ms = ema(series, slow)
    line = mf - ms
    sig  = line.ewm(span=signal, adjust=False).mean()
    hist = line - sig
    return line, sig, hist

def bollinger(series: pd.Series, length=20, k=2.0):
    mid = series.rolling(length).mean()
    std = series.rolling(length).std(ddof=0)
    upper = mid + k*std
    lower = mid - k*std
    return lower, mid, upper


def compute_score_df(df: pd.DataFrame) -> pd.DataFrame:
    c = df['close']
    df['ema20'] = ema(c, 20)
    df['ema50'] = ema(c, 50)
    df['bb_low'], df['bb_mid'], df['bb_high'] = bollinger(c, 20, 2.0)
    df['rsi'] = rsi(c, 14)
    df['macd'], df['macd_signal'], df['macd_hist'] = macd(c, 12, 26, 9)

    # conditions (True -> 1, False -> 0)
    cond_ema  = (df['ema20'] > df['ema50']).astype(int)
    cond_bb   = (df['close'] <= df['bb_low']).astype(int)
    cond_rsi  = (df['rsi'] < 35).astype(int)
    cond_macd = ((df['macd'] > df['macd_signal']) & (df['macd_hist'] > 0)).astype(int)

    df['score'] = (
        W_EMA*cond_ema + W_BB*cond_bb + W_RSI*cond_rsi + W_MACD*cond_macd
    ).astype(int)

    return df.dropna().copy()


def write_json_from_df(df: pd.DataFrame):
    # Keep only last 12h (POINTS bars)
    tail = df.tail(POINTS)
    data = [{'t': d.isoformat(timespec='seconds').replace('+00:00','Z'),
             'close': float(row.close),
             'score': int(row.score)}
            for d, row in zip(tail['ts'], tail.itertuples())]
    DOCS.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(data), encoding='utf-8')


def write_html():
    # Simple 2-axis chart: price (left), score 0-100 (right)
    html = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>BTC Close + Strong Buy Score (last 12h)</title>
<style>
  body{font-family:-apple-system,Helvetica,Arial,sans-serif;margin:24px}
  h2{margin:0 0 12px}
  #wrap{max-width:1100px;margin:0 auto}
  canvas{width:100%;height:440px}
  small{color:#666}
</style>
</head>
<body>
  <div id="wrap">
    <h2>BTC/USDT — Price and Strong Buy score (last 12 hours)</h2>
    <small>Left axis: price. Right axis: Strong Buy score (0–100).</small>
    <canvas id="c"></canvas>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <script>
  (function(){
    'use strict';
    fetch('btc.json?t=' + Date.now(), {cache:'no-store'})
      .then(function(r){ return r.json(); })
      .then(function(rows){
        var labels = rows.map(function(p){ return p.t; });
        var price  = rows.map(function(p){ return p.close; });
        var score  = rows.map(function(p){ return p.score; });

        var ctx = document.getElementById('c').getContext('2d');
        new Chart(ctx, {
          type: 'line',
          data: {
            labels: labels,
            datasets: [{
              label: 'BTC/USDT close',
              yAxisID: 'price',
              data: price,
              borderColor: '#1f77b4',
              backgroundColor: '#1f77b4',
              pointRadius: 0,
              tension: 0.25,
              spanGaps: true
            },{
              label: 'Strong Buy score',
              yAxisID: 'score',
              data: score,
              borderColor: '#d62728',
              backgroundColor: '#d62728',
              pointRadius: 0,
              tension: 0.25,
              spanGaps: true
            }]
          },
          options: {
            responsive: true,
            scales: {
              x: { ticks: { maxTicksLimit: 25 } },
              price: {
                type: 'linear',
                position: 'left'
              },
              score: {
                type: 'linear',
                position: 'right',
                min: 0,
                max: 100,
                grid: { drawOnChartArea: false }  // keep grid tidy
              }
            },
            interaction: { mode: 'nearest', intersect: false }
          }
        });
      })
      .catch(function(e){ console.error(e); });
  })();
  </script>
</body>
</html>"""
    HTML_PATH.write_text(html, encoding='utf-8')


def git_push():
    try:
        subprocess.run(['git','add','docs'], check=True)
        subprocess.run(['git','commit','-m', 'Update BTC price + score chart'], check=True)
        subprocess.run(['git','push'], check=True)
        print('✅ Pushed to GitHub.')
    except Exception as e:
        print('⚠️ Git push skipped or failed:', e)


def main():
    print('Fetching BTC/USDT 15m…')
    df = fetch_btc_df()
    print('Bars fetched:', len(df))
    df = compute_score_df(df)
    print('Bars after indicators:', len(df))
    write_json_from_df(df)
    write_html()
    git_push()
    print('Done.')

if __name__ == '__main__':
    main()
