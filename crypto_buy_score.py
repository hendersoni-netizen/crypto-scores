#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ---- SIMPLE BTC-ONLY SITE ----
# - Fetches last 12h of BTC/USDT 15m candles from Binance (via ccxt)
# - Writes docs/btc.json
# - Writes docs/index.html (a tiny page with one Chart.js line chart)
# - Git add/commit/push

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import ccxt

# ---------- Settings ----------
EXCHANGE = ccxt.binance({'enableRateLimit': True})
SYMBOL   = 'BTC/USDT'
TIMEFRAME = '15m'
HOURS = 12
POINTS = HOURS * 60 // 15  # 48 points

ROOT = Path('.')
DOCS = ROOT/'docs'
JSON_PATH = DOCS/'btc.json'
HTML_PATH = DOCS/'index.html'


def iso_utc(ts_ms: int) -> str:
    """ms -> 'YYYY-MM-DDTHH:MM:SSZ'"""
    return datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def fetch_btc():
    # a few extra bars for safety
    limit = POINTS + 5
    ohlcv = EXCHANGE.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=limit)
    data = [{'t': iso_utc(ts), 'close': float(close)} for ts, _o, _h, _l, close, _v in ohlcv]
    return data[-POINTS:]  # last 12h only


def write_json(data):
    DOCS.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(data), encoding='utf-8')


def write_html():
    # Very small page: 1 chart, no timers, no table
    html = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>BTC Close (last 12h)</title>
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
    <h2>BTC/USDT — Close (last 12 hours)</h2>
    <small>Data updates when you run the script.</small>
    <canvas id="c"></canvas>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <script>
  (function(){
    'use strict';
    // super simple: fetch JSON and plot; category x-axis (no date adapter needed)
    fetch('btc.json?t=' + Date.now(), {cache:'no-store'})
      .then(function(r){ return r.json(); })
      .then(function(rows){
        var labels = rows.map(function(p){ return p.t; });
        var data   = rows.map(function(p){ return p.close; });

        var ctx = document.getElementById('c').getContext('2d');
        new Chart(ctx, {
          type: 'line',
          data: {
            labels: labels,
            datasets: [{
              label: 'BTC/USDT close',
              data: data,
              borderColor: '#1f77b4',
              backgroundColor: '#1f77b4',
              pointRadius: 0,
              tension: 0.25,
              spanGaps: true
            }]
          },
          options: {
            responsive: true,
            scales: {
              x: {ticks: {maxTicksLimit: 25}},   // keep it readable
              y: {beginAtZero: false}
            }
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
        subprocess.run(['git','commit','-m', 'Update BTC chart'], check=True)
        subprocess.run(['git','push'], check=True)
        print('✅ Pushed to GitHub.')
    except Exception as e:
        print('⚠️ Git push skipped or failed:', e)


def main():
    print('Fetching BTC/USDT 15m…')
    rows = fetch_btc()
    print('Points:', len(rows))
    write_json(rows)
    write_html()
    git_push()
    print('Done. Open your GitHub Pages site to see the chart.')

if __name__ == '__main__':
    main()
