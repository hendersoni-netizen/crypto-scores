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

# -----------------------------
# CONFIG
# -----------------------------
EXCHANGE_ID = "binance"
SYMBOLS = ["BTC/USDT", "ETH/USDT", "ONDO/USDT"]

TIMEFRAME = "15m"
LOOKBACK_HOURS = 12
UI_INTERVAL_MINUTES = 15

CSV_PATH = Path("docs/buy_scores.csv")
HTML_PATH = Path("docs/index.html")

W_EMA = 25
W_BB  = 25
W_RSI = 25
W_MACD= 25

exchange = getattr(ccxt, EXCHANGE_ID)({"enableRateLimit": True})

def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def iso_utc_now() -> str:
    return iso_utc(datetime.now(timezone.utc))

# -----------------------------
# Indicators
# -----------------------------
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

# -----------------------------
# Data + score
# -----------------------------
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

# -----------------------------
# Out
