#!/usr/bin/env python3
"""국내주식 스윙 '필승 조건' 백테스트 엔진 (무료 데이터 전용).

데이터 (전부 무료, 로그인·유료결제 없음)
  - 유니버스/시가총액: 네이버 증권 모바일 API (m.stock.naver.com)
  - 일별 원주가 OHLC·거래대금: 다음 금융 차트 API (finance.daum.net, adjusted=false)
  - 일별 **수정주가**·외국인소지율: 네이버 siseJson API
      액면분할·무상증자·감자 등 가격 조정이 반영된 수정주가로 이평선·이격도·수익률을 전부 계산한다.
      (배당락 조정은 반영되지 않음 → 배당수익률만큼 보수적으로 과소평가)
  - 코스피/코스닥 지수: Yahoo Finance ^KS11 / ^KQ11

비용 (순수익 기준)
  - 슬리피지 매수·매도 각 0.10% 기본 (0.20%·0.30% 민감도 동시 산출)
  - 수수료 매수·매도 각 0.015%
  - 증권거래세: 매도 연도별 세율
      2015~2019 0.30% / 2020 0.25% / 2021~2022 0.23% / 2023 0.20% / 2024 0.18% / 2025 0.15% / 2026 0.20%
      (2026-01-02 시행: 코스피 0.05%+농특세 0.15%, 코스닥 0.20% — 증권사 공지·증권거래세법 시행령)

사용법
  python3 scripts/backtest.py --stage fetch            # 수집(중단 후 재실행하면 이어받음)
  python3 scripts/backtest.py --stage run              # 전체 측정 → data/backtest.json + backtest-signals.json
  python3 scripts/backtest.py --stage run --offset 0 --limit 1300 --shard 0   # 샤드 실행(오래 걸릴 때)
  python3 scripts/backtest.py --stage merge            # 샤드 합치고 JSON 출력
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import pickle
import re
import time
from array import array
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ----------------------------------------------------------------------------
# 경로·설정
# ----------------------------------------------------------------------------
REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("BACKTEST_CACHE", REPO.parent / "work" / "backtest" / "cache"))
(CACHE / "ohlcv").mkdir(parents=True, exist_ok=True)
(CACHE / "shards").mkdir(parents=True, exist_ok=True)
OUT_JSON = REPO / "data" / "backtest.json"
OUT_SIGNALS = REPO / "data" / "backtest-signals.json"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
SESSION = requests.Session()
SESSION.headers.update(UA)

START, END = "2015-01-01", "2026-11-20"

MIN_MKTCAP = 50_000_000_000     # 시가총액 500억
MIN_VALUE = 10_000_000_000      # 거래대금 100억
MAX_DISP20 = 0.15               # 20일선 이격도 +15% 초과 = 과열 제외

HORIZONS = (5, 10, 20)
STOPS = (0.05, 0.07)
COOLDOWN = 10
MIN_SAMPLE = 30

SLIP_BASE = 0.001
SLIPS = (0.001, 0.002, 0.003)
COMM = 0.00015
TAX_BY_YEAR = {2015: 0.0030, 2016: 0.0030, 2017: 0.0030, 2018: 0.0030, 2019: 0.0030,
               2020: 0.0025, 2021: 0.0023, 2022: 0.0023, 2023: 0.0020, 2024: 0.0018,
               2025: 0.0015, 2026: 0.0020}

RECENT_MONTHS = 3   # 모든 조건의 개별 신호는 최근 3개월치를 저장(최종안은 전 기간)
ENTRY_MODE = os.environ.get("BACKTEST_ENTRY", "next_open")   # next_open | next_close | close

PREFERRED_RE = re.compile(r"(?:\d?우B?C?)$|우\(|우B$")
NON_COMMON_KEYWORDS = ("스팩", "리츠", "인프라", "ETN", "ETF", "선물", "레버리지", "인버스", "합성")


# ----------------------------------------------------------------------------
# 수집
# ----------------------------------------------------------------------------
def _get(url: str, tries: int = 4, **kw) -> requests.Response:
    last = None
    for i in range(tries):
        try:
            r = SESSION.get(url, timeout=30, **kw)
            if r.status_code == 200:
                return r
            last = RuntimeError(f"HTTP {r.status_code} {url}")
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"fail {url}: {last}")


def fetch_universe() -> pd.DataFrame:
    path = CACHE / "universe.pkl"
    if path.exists():
        return pd.read_pickle(path)
    rows = []
    for mkt in ("KOSPI", "KOSDAQ"):
        page, got = 1, 0
        while True:
            r = _get(f"https://m.stock.naver.com/api/stocks/marketValue/{mkt}?page={page}&pageSize=100")
            j = r.json()
            stocks = j.get("stocks") or []
            if not stocks:
                break
            for s in stocks:
                rows.append({
                    "code": s.get("itemCode"), "name": s.get("stockName"), "market": mkt,
                    "end_type": s.get("stockEndType"),
                    "mktcap_eok": pd.to_numeric(str(s.get("marketValue") or "").replace(",", ""), errors="coerce"),
                    "last_close": pd.to_numeric((s.get("closePrice") or "").replace(",", ""), errors="coerce"),
                    "tradable": s.get("tradableStatus") or "",
                })
            got += len(stocks)
            if got >= j.get("totalCount", 0) or len(stocks) < 100:
                break
            page += 1
            time.sleep(0.15)
    df = pd.DataFrame(rows).drop_duplicates("code")
    df.to_pickle(path)
    return df


def is_common_stock(name: str, end_type: str) -> bool:
    if end_type != "stock":
        return False
    if PREFERRED_RE.search(name or ""):
        return False
    if any(k in (name or "") for k in NON_COMMON_KEYWORDS):
        return False
    return True


def fetch_index() -> pd.DataFrame:
    path = CACHE / "index.pkl"
    if path.exists():
        return pd.read_pickle(path)
    out = {}
    for sym, col in (("^KS11", "kospi"), ("^KQ11", "kosdaq")):
        p1, p2 = 1420070400, 1795300000
        r = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d")
        res = r.json()["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        df = pd.DataFrame({
            "date": pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert("Asia/Seoul").normalize().tz_localize(None),
            "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"],
        }).dropna(subset=["close"]).set_index("date").sort_index()
        out[col] = df
    merged = out["kospi"].join(out["kosdaq"], how="outer", lsuffix="_k", rsuffix="_q").sort_index()
    merged.to_pickle(path)
    return merged


def fetch_sise_json(code: str, start: str, end: str) -> pd.DataFrame:
    url = (f"https://api.finance.naver.com/siseJson.naver?symbol={code}&requestType=1"
           f"&startTime={start.replace('-', '')}&endTime={end.replace('-', '')}&timeframe=day")
    r = _get(url)
    txt = r.text.strip()
    if not txt.startswith("["):
        return pd.DataFrame()
    data = ast.literal_eval(re.sub(r"\s+", "", txt))
    if len(data) < 2:
        return pd.DataFrame()
    rows = [x for x in data[1:] if isinstance(x, list) and len(x) >= 6]
    df = pd.DataFrame(rows)
    if df.shape[1] >= 7:
        df = df.iloc[:, :7]
        df.columns = ["date", "open", "high", "low", "close", "volume", "foreign_pct"]
    else:
        df = df.iloc[:, :6]
        df.columns = ["date", "open", "high", "low", "close", "volume"]
        df["foreign_pct"] = np.nan
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d", errors="coerce")
    for c in ["open", "high", "low", "close", "volume", "foreign_pct"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date"])
    df = df[(df["close"] > 0) & (df["volume"] > 0)]
    return df.set_index("date").sort_index()


def fetch_daum(code: str) -> pd.DataFrame:
    h = dict(UA)
    h["Referer"] = f"https://finance.daum.net/quotes/A{code}"
    r = SESSION.get(f"https://finance.daum.net/api/charts/A{code}/days?limit=3000&adjusted=false", headers=h, timeout=40)
    if r.status_code != 200:
        return pd.DataFrame()
    data = (r.json() or {}).get("data") or []
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    if not {"date", "openingPrice", "highPrice", "lowPrice", "tradePrice", "candleAccTradeVolume"}.issubset(df.columns):
        return pd.DataFrame()
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"], errors="coerce"),
        "open": pd.to_numeric(df["openingPrice"], errors="coerce"),
        "high": pd.to_numeric(df["highPrice"], errors="coerce"),
        "low": pd.to_numeric(df["lowPrice"], errors="coerce"),
        "close": pd.to_numeric(df["tradePrice"], errors="coerce"),
        "volume": pd.to_numeric(df["candleAccTradeVolume"], errors="coerce"),
        "turnover": pd.to_numeric(df.get("candleAccTradePrice"), errors="coerce"),
    }).set_index("date")
    out = out.dropna(subset=["close"])
    return out[(out["close"] > 0) & (out["volume"] > 0)].sort_index()


def fetch_one(code: str, start: str, end: str) -> pd.DataFrame:
    raw = fetch_daum(code)
    naver = fetch_sise_json(code, start, end)
    if raw.empty and naver.empty:
        return pd.DataFrame()
    if naver.empty:
        naver = pd.DataFrame({"aopen": raw["open"], "ahigh": raw["high"], "alow": raw["low"], "aclose": raw["close"]})
    else:
        naver = naver.rename(columns={"open": "aopen", "high": "ahigh", "low": "alow", "close": "aclose"})
        naver = naver.drop(columns=["volume"], errors="ignore")
    if raw.empty:
        raw = pd.DataFrame(index=naver.index)
        raw["turnover"] = np.nan
        raw["volume"] = np.nan
    cols = [c for c in ["open", "high", "low", "close", "volume", "turnover"] if c in raw]
    df = naver.join(raw[cols], how="left")
    if df["turnover"].isna().all():
        base = df["close"] if "close" in df else df["aclose"]
        df["turnover"] = base.fillna(df["aclose"]) * df["volume"].fillna(0)
    df["volume"] = df["volume"].fillna(0)
    return df.sort_index()


def stage_fetch(codes, start, end, workers, refresh):
    todo = [c for c in codes if refresh or not (CACHE / "ohlcv" / f"{c}.pkl").exists()]
    print(f"[fetch] target {len(todo)} / total {len(codes)}", flush=True)
    done = fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_one, c, start, end): c for c in todo}
        for fut in as_completed(futs):
            code = futs[fut]
            try:
                df = fut.result()
            except Exception as e:  # noqa: BLE001
                df = pd.DataFrame()
                print(f"  !! {code} {e}", flush=True)
            df.to_pickle(CACHE / "ohlcv" / f"{code}.pkl")
            fail += int(df.empty)
            done += 1
            if done % 400 == 0:
                print(f"[fetch] {done}/{len(todo)}  빈응답 {fail}  {time.time()-t0:.0f}s", flush=True)
    print(f"[fetch] done {done}, empty {fail}", flush=True)


# ----------------------------------------------------------------------------
# 지표 & 조건
# ----------------------------------------------------------------------------
def build_features(df: pd.DataFrame, shares: float, idx: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c, h, v = d["aclose"], d["ahigh"], d["volume"]
    d["ma5"] = c.rolling(5).mean()
    d["ma20"] = c.rolling(20).mean()
    d["ma60"] = c.rolling(60).mean()
    d["disp20"] = c / d["ma20"] - 1
    d["vol_ma20"] = v.rolling(20).mean()
    d["vmult"] = v / d["vol_ma20"]
    d["high250"] = h.rolling(250).max().shift(1)
    d["turn_ma20"] = d["turnover"].rolling(20).mean()
    d["turn_mult"] = d["turnover"] / d["turn_ma20"]
    d["mktcap"] = c * shares
    d["f_up3"] = (d["foreign_pct"].diff() > 0).rolling(3).sum() == 3
    d["f_up5"] = (d["foreign_pct"].diff() > 0).rolling(5).sum() == 5
    k = idx["close_k"].reindex(d.index).ffill()
    d["kospi"] = k
    d["kospi_ma20"] = k.rolling(20).mean()
    d["bull_market"] = (k > d["kospi_ma20"]).fillna(False)
    d["align"] = ((d["ma5"] > d["ma20"]) & (d["ma20"] > d["ma60"])).fillna(False)
    d["align_prev"] = d["align"].shift(1, fill_value=False)
    d["above20"] = (c > d["ma20"]).fillna(False)
    d["above20_prev"] = d["above20"].shift(1, fill_value=False)
    d["above60"] = (c > d["ma60"]).fillna(False)
    d["above60_prev"] = d["above60"].shift(1, fill_value=False)
    d["up_candle"] = (d["aclose"] > d["aopen"]) & (d["aclose"] > d["aclose"].shift(1))
    d["pull_ready"] = d["aclose"].shift(1) <= d["ma5"].shift(1)
    return d


def cond_defs() -> list[dict]:
    def cross20(d):
        return d["above20"] & (~d["above20_prev"])

    def cross60(d):
        return d["above60"] & (~d["above60_prev"])

    def new_high(d):
        return d["aclose"] > d["high250"]

    def disp(lo, hi):
        return lambda d: (d["disp20"] > lo) & (d["disp20"] <= hi)

    def vol(m):
        return lambda d: d["vmult"] >= m

    C = []

    def add(cid, name, rule, fn, ctype="trend"):
        C.append({"id": cid, "name": name, "rule": rule, "fn": fn, "type": ctype})

    add("C01", "20일선 돌파 + 거래량 1.5배", "종가>MA20 & 전일 종가<=전일 MA20 & 거래량>=MA20거래량×1.5 & 0<이격도<=15%",
        lambda d: cross20(d) & vol(1.5)(d) & disp(0, MAX_DISP20)(d))
    add("C02", "20일선 돌파 + 거래량 2배", "C01에서 거래량 배수 2.0",
        lambda d: cross20(d) & vol(2.0)(d) & disp(0, MAX_DISP20)(d))
    add("C03", "20일선 돌파 + 거래량 3배", "C01에서 거래량 배수 3.0",
        lambda d: cross20(d) & vol(3.0)(d) & disp(0, MAX_DISP20)(d))
    add("C04", "60일선 돌파 + 거래량 1.5배", "종가>MA60 & 전일 종가<=전일 MA60 & 거래량>=MA20거래량×1.5 & 이격도<=15%",
        lambda d: cross60(d) & vol(1.5)(d) & (d["disp20"] <= MAX_DISP20))
    add("C05", "52주 신고가 돌파 + 거래량 1.5배", "종가>직전 250거래일 최고가 & 거래량>=MA20거래량×1.5 & 이격도<=15%",
        lambda d: new_high(d) & vol(1.5)(d) & (d["disp20"] <= MAX_DISP20))
    add("C06", "52주 신고가 돌파 + 거래량 2배", "C05에서 거래량 배수 2.0",
        lambda d: new_high(d) & vol(2.0)(d) & (d["disp20"] <= MAX_DISP20))
    add("C07", "정배열 진입(5>20>60) + 거래량 1.5배", "MA5>MA20>MA60 당일 성립·전일 불성립 & 거래량×1.5 & 이격도<=15%",
        lambda d: d["align"] & (~d["align_prev"]) & vol(1.5)(d) & (d["disp20"] <= MAX_DISP20))
    add("C08", "정배열 + 이격도 0~5%", "(5>20>60) & 0<이격도<=5% & 거래량×1.5",
        lambda d: d["align"] & disp(0, 0.05)(d) & vol(1.5)(d))
    add("C09", "정배열 + 이격도 5~10%", "(5>20>60) & 5%<이격도<=10% & 거래량×1.5",
        lambda d: d["align"] & disp(0.05, 0.10)(d) & vol(1.5)(d))
    add("C10", "정배열 + 이격도 10~15%", "(5>20>60) & 10%<이격도<=15% & 거래량×1.5",
        lambda d: d["align"] & disp(0.10, 0.15)(d) & vol(1.5)(d))
    add("C11", "20일선 돌파 + 이격도 0~5%", "C01 중 이격도 0~5%",
        lambda d: cross20(d) & vol(1.5)(d) & disp(0, 0.05)(d))
    add("C12", "20일선 돌파 + 이격도 5~10%", "C01 중 이격도 5~10%",
        lambda d: cross20(d) & vol(1.5)(d) & disp(0.05, 0.10)(d))
    add("C13", "20일선 돌파 + 이격도 10~15%", "C01 중 이격도 10~15%",
        lambda d: cross20(d) & vol(1.5)(d) & disp(0.10, 0.15)(d))
    add("C14", "외국인 3일 연속 보유율 상승", "종가>MA20 & 외국인소지율 3거래일 연속 상승",
        lambda d: d["f_up3"] & d["above20"], "flow")
    add("C15", "외국인 5일 연속 보유율 상승", "종가>MA20 & 외국인소지율 5거래일 연속 상승",
        lambda d: d["f_up5"] & d["above20"], "flow")
    add("C16", "20일선 돌파 + 외국인 3일 연속", "C01에서 거래량 조건 대신 외국인 3일 연속 상승",
        lambda d: cross20(d) & d["f_up3"] & (d["disp20"] <= MAX_DISP20), "flow")
    add("C17", "눌림목(정배열+이격도 2~10%+반등양봉)", "정배열 & 2%<이격도<=10% & 양봉 & 종가>전일종가 & 전일 종가<=전일 MA5",
        lambda d: d["align"] & disp(0.02, 0.10)(d) & d["up_candle"] & d["pull_ready"], "pullback")
    add("C18", "20일선 돌파 + 시장필터(코스피>MA20)", "C01 & 신호일 코스피>코스피 MA20",
        lambda d: cross20(d) & vol(1.5)(d) & (d["disp20"] <= MAX_DISP20) & d["bull_market"], "filter")
    add("C19", "20일선 돌파 + 코스피<MA20(비교군)", "C01 & 신호일 코스피<=코스피 MA20",
        lambda d: cross20(d) & vol(1.5)(d) & (d["disp20"] <= MAX_DISP20) & (~d["bull_market"]), "filter")
    add("C20", "거래대금 3배 급증 + 종가>MA20", "거래대금>=MA20거래대금×3 & 종가>MA20 & 이격도<=15%",
        lambda d: (d["turn_mult"] >= 3.0) & d["above20"] & (d["disp20"] <= MAX_DISP20), "volume")
    add("C21", "60일선 돌파 + 정배열", "C04 + (5>20>60)",
        lambda d: cross60(d) & d["align"] & (d["disp20"] <= MAX_DISP20))
    add("C22", "52주 신고가 + 외국인 3일 연속", "C05 + 외국인 3일 연속 상승",
        lambda d: new_high(d) & d["f_up3"] & (d["disp20"] <= MAX_DISP20), "combo")
    add("C23", "눌림목 + 외국인 3일 연속", "C17 + 외국인 3일 연속 상승",
        lambda d: d["align"] & disp(0.02, 0.10)(d) & d["up_candle"] & d["pull_ready"] & d["f_up3"], "combo")
    add("C24", "20일선 돌파 + 거래량2배 + 시장필터 + 외국인3일", "C02 + 코스피>MA20 + 외국인 3일 연속",
        lambda d: cross20(d) & vol(2.0)(d) & (d["disp20"] <= MAX_DISP20) & d["bull_market"] & d["f_up3"], "combo")
    add("C25", "정배열 유지 + 이격도 0~10% + 거래량1.5배", "(5>20>60) & 0<이격도<=10% & 거래량×1.5",
        lambda d: d["align"] & disp(0, 0.10)(d) & vol(1.5)(d))
    add("C26", "52주 신고가 + 시장필터", "C05 & 코스피>MA20",
        lambda d: new_high(d) & (d["disp20"] <= MAX_DISP20) & d["bull_market"], "filter")
    add("C27", "20일선 돌파 단독(거래량 조건 없음)", "종가>MA20 & 전일 종가<=전일 MA20 & 이격도<=15%",
        lambda d: cross20(d) & (d["disp20"] <= MAX_DISP20))
    return C


# ----------------------------------------------------------------------------
# 누적기
# ----------------------------------------------------------------------------
class Acc:
    """조건별 거래 결과 누적 (메모리 절약을 위해 array 사용)."""

    __slots__ = ("r", "rn", "mae", "exc", "excm", "sl", "years", "detail")

    def __init__(self):
        self.r = {h: array("d") for h in HORIZONS}
        self.rn = {s: {h: array("d") for h in HORIZONS} for s in SLIPS}
        self.mae = {h: array("d") for h in HORIZONS}
        self.exc = {h: array("d") for h in HORIZONS}
        self.excm = {h: array("d") for h in HORIZONS}
        self.sl = {(s, stop): array("d") for s in SLIPS for stop in STOPS}
        self.years = array("i")
        self.detail: list[tuple] = []

    def dump(self):
        return {
            "r": {h: np.frombuffer(a, dtype="d").copy() for h, a in self.r.items()},
            "rn": {s: {h: np.frombuffer(a, dtype="d").copy() for h, a in d.items()} for s, d in self.rn.items()},
            "mae": {h: np.frombuffer(a, dtype="d").copy() for h, a in self.mae.items()},
            "exc": {h: np.frombuffer(a, dtype="d").copy() for h, a in self.exc.items()},
            "excm": {h: np.frombuffer(a, dtype="d").copy() for h, a in self.excm.items()},
            "sl": {k: np.frombuffer(a, dtype="d").copy() for k, a in self.sl.items()},
            "years": np.frombuffer(self.years, dtype="i").copy(),
            "detail": self.detail,
        }

    @staticmethod
    def load(state) -> "Acc":
        a = Acc()
        for h, v in state["r"].items():
            a.r[h] = array("d", v.tolist())
        for s, d in state["rn"].items():
            for h, v in d.items():
                a.rn[s][h] = array("d", v.tolist())
        for h, v in state["mae"].items():
            a.mae[h] = array("d", v.tolist())
        for h, v in state["exc"].items():
            a.exc[h] = array("d", v.tolist())
        for h, v in state["excm"].items():
            a.excm[h] = array("d", v.tolist())
        for k, v in state["sl"].items():
            a.sl[k] = array("d", v.tolist())
        a.years = array("i", state["years"].tolist())
        a.detail = state["detail"]
        return a

    def merge(self, other: "Acc") -> None:
        for h in HORIZONS:
            self.r[h].extend(other.r[h])
            self.mae[h].extend(other.mae[h])
            self.exc[h].extend(other.exc[h])
            self.excm[h].extend(other.excm[h])
            for s in SLIPS:
                self.rn[s][h].extend(other.rn[s][h])
        for k in self.sl:
            self.sl[k].extend(other.sl[k])
        self.years.extend(other.years)
        self.detail.extend(other.detail)


# ----------------------------------------------------------------------------
# 시뮬레이션
# ----------------------------------------------------------------------------
def simulate(mask: np.ndarray, d: pd.DataFrame, idx: pd.DataFrame, acc: Acc,
             export_all: bool, since_ts: pd.Timestamp, market: str = "KOSPI") -> None:
    """신호 마스크 → 거래 집계. 진입=신호 다음 거래일 시가, 청산=보유 N거래일 종가(수정주가)."""
    dates = d.index
    a_open, a_close, a_low = d["aopen"].to_numpy(), d["aclose"].to_numpy(), d["alow"].to_numpy()
    kos = idx["close_k"].reindex(dates).ffill().to_numpy()
    kos_o = idx["open_k"].reindex(dates).ffill().to_numpy()
    suf = "q" if market == "KOSDAQ" else "k"
    own = idx[f"close_{suf}"].reindex(dates).ffill().to_numpy()
    own_o = idx[f"open_{suf}"].reindex(dates).ffill().to_numpy()
    n = len(a_close)
    last = -(10**9)
    for t in np.flatnonzero(mask):
        if t - last < COOLDOWN or t + 1 >= n:
            continue
        if ENTRY_MODE == "close":
            entry = a_close[t]
        elif ENTRY_MODE == "next_close":
            entry = a_close[t + 1]
        else:
            entry = a_open[t + 1]
        if not np.isfinite(entry) or entry <= 0:
            continue
        last = t
        if t + 1 >= n:
            continue
        do_export = export_all or dates[t] >= since_ts
        rec = None
        got = False
        for h in HORIZONS:
            k = t + h
            if k >= n or not np.isfinite(a_close[k]):
                continue
            seg = a_low[t + 1: k + 1]
            if len(seg) == 0:
                continue
            r = a_close[k] / entry - 1
            tax = TAX_BY_YEAR.get(dates[k].year, 0.0020)
            acc.r[h].append(float(r))
            acc.mae[h].append(float(seg.min() / entry - 1))
            if np.isfinite(kos[k]) and kos_o[t + 1] > 0:
                acc.exc[h].append(float(r - (kos[k] / kos_o[t + 1] - 1)))
            if np.isfinite(own[k]) and own_o[t + 1] > 0:
                acc.excm[h].append(float(r - (own[k] / own_o[t + 1] - 1)))
            for slip in SLIPS:
                buy_f = (1 + slip) * (1 + COMM)
                sell_f = (1 - slip) * (1 - COMM - tax)
                rn = (a_close[k] * sell_f) / (entry * buy_f) - 1
                acc.rn[slip][h].append(float(rn))
                if h == 20:
                    for stop in STOPS:
                        stop_px = entry * (1 - stop)
                        if (seg <= stop_px).any():
                            acc.sl[(slip, stop)].append(float((stop_px * sell_f) / (entry * buy_f) - 1))
                        else:
                            acc.sl[(slip, stop)].append(float(rn))
                if h == 20 and slip == SLIP_BASE:
                    acc.years.append(int(dates[k].year))
            if do_export:
                # 순수익(기본 슬리피지) 기준 기록
                buy_f = (1 + SLIP_BASE) * (1 + COMM)
                sell_f = (1 - SLIP_BASE) * (1 - COMM - tax)
                rec = rec or {}
                rec[h] = (a_close[k] * sell_f) / (entry * buy_f) - 1
            got = True
        if got and do_export:
            rvals = [round(rec[h] * 100, 2) if rec and h in rec else None for h in HORIZONS]
            acc.detail.append((int(dates[t].strftime("%Y%m%d")), int(dates[t + 1].strftime("%Y%m%d")),
                               float(entry), rvals[0], rvals[1], rvals[2]))


def _basic(v):
    v = np.asarray(v, dtype=float)
    if len(v) == 0:
        return None
    w = v > 0
    aw = v[w].mean() if w.any() else 0.0
    al = -v[~w].mean() if (~w).any() else 1e-9
    return {"n": int(len(v)), "mean": float(v.mean()), "median": float(np.median(v)),
            "win": float(w.mean()), "payoff": float(aw / al) if al > 0 else None,
            "avg_win": float(aw), "avg_loss": float(-al)}


def summarize(acc: Acc) -> dict:
    m = {"signals": len(acc.r[20])}
    for h in HORIZONS:
        g = _basic(acc.r[h])
        if g is None:
            m[f"h{h}"] = None
            continue
        m[f"h{h}"] = {
            "n": g["n"],
            "gross": {k: g[k] for k in ("mean", "median", "win", "payoff")},
            "net": {k: _basic(acc.rn[SLIP_BASE][h])[k] for k in ("mean", "median", "win", "payoff")},
            "mae_mean": float(np.asarray(acc.mae[h]).mean()) if len(acc.mae[h]) else None,
            "mae_p10": float(np.percentile(np.asarray(acc.mae[h]), 10)) if len(acc.mae[h]) else None,
            "excess": float(np.asarray(acc.exc[h]).mean()) if len(acc.exc[h]) else None,
            "excess_win": float((np.asarray(acc.exc[h]) > 0).mean()) if len(acc.exc[h]) else None,
            "excess_mkt": float(np.asarray(acc.excm[h]).mean()) if len(acc.excm[h]) else None,
            "excess_mkt_win": float((np.asarray(acc.excm[h]) > 0).mean()) if len(acc.excm[h]) else None,
        }
    for stop in STOPS:
        v = _basic(acc.sl[(SLIP_BASE, stop)])
        if v:
            m[f"h20_sl{int(stop*100)}"] = {"n": v["n"], "net_mean": v["mean"], "net_win": v["win"], "net_payoff": v["payoff"]}
    # 연도별 20일 순수익
    rn20 = np.asarray(acc.rn[SLIP_BASE][20])
    yrs = np.asarray(acc.years)
    by_year = {}
    for y in np.unique(yrs):
        v = rn20[yrs == y]
        if len(v):
            by_year[str(int(y))] = {"n": int(len(v)), "mean": float(v.mean()), "win": float((v > 0).mean())}
    m["by_year_net20"] = by_year
    return m


# ----------------------------------------------------------------------------
# 실행
# ----------------------------------------------------------------------------
def prepare_universe() -> pd.DataFrame:
    universe = fetch_universe()
    uni = universe[universe.apply(lambda r: is_common_stock(r["name"], r["end_type"]), axis=1)].copy()
    uni = uni[uni["last_close"] > 0]
    if "shares" not in uni:
        uni["shares"] = uni["mktcap_eok"] * 1e8 / uni["last_close"]
    return uni.sort_values("mktcap_eok", ascending=False).reset_index(drop=True)


def load_frame(code: str, shares: float, idx: pd.DataFrame):
    p = CACHE / "ohlcv" / f"{code}.pkl"
    if not p.exists():
        return None
    df = pd.read_pickle(p)
    if df.empty or len(df) < 260:
        return None
    return build_features(df, shares, idx)


def run_pass(uni, idx, specs, export_ids, since_ts, verbose=True, cap_floor=MIN_MKTCAP):
    accs = {s["id"]: Acc() for s in specs}
    used = 0
    names = {}
    t0 = time.time()
    for i, row in enumerate(uni.itertuples()):
        d = load_frame(row.code, row.shares, idx)
        if d is None:
            continue
        used += 1
        names[row.code] = (row.name, row.market)
        uni_ok = ((d["mktcap"] >= cap_floor) & (d["turnover"] >= MIN_VALUE) & (d["volume"] > 0)).to_numpy()
        for s in specs:
            try:
                mask = s["fn"](d).fillna(False).to_numpy() & uni_ok
            except Exception:  # noqa: BLE001
                continue
            if not mask.any():
                continue
            before = len(accs[s["id"]].detail)
            simulate(mask, d, idx, accs[s["id"]], export_all=s["id"] in export_ids, since_ts=since_ts,
                     market=row.market)
            for j in range(before, len(accs[s["id"]].detail)):
                accs[s["id"]].detail[j] = (row.code,) + accs[s["id"]].detail[j]
        if verbose and (i + 1) % 500 == 0:
            print(f"[eval] {i+1}/{len(uni)} 사용 {used}  {time.time()-t0:.0f}s", flush=True)
    return accs, used, names


def compose(spec: dict, base: dict):
    def fn(d):
        m = None
        for i in spec.get("or", []):
            mm = base[i](d)
            m = mm if m is None else (m | mm)
        for i in spec.get("and", []):
            mm = base[i](d)
            m = mm if m is None else (m & mm)
        return m
    return fn


COMBO_SPECS = [
    {"id": "X1", "name": "52주신고가+시장필터 & 외국인3일", "and": ["C26", "C22"]},
    {"id": "X2", "name": "52주신고가+시장필터 & 거래량2배", "and": ["C26", "C06"]},
    {"id": "X3", "name": "20일선돌파+이격도5~10% & 시장필터", "and": ["C12", "C18"]},
    {"id": "X4", "name": "52주신고가+시장필터 OR 20일선돌파+이격도5~10%", "or": ["C26", "C12"]},
    {"id": "X5", "name": "52주신고가+시장필터 & 정배열진입", "and": ["C26", "C07"]},
    {"id": "X6", "name": "20일선돌파+거래량3배 & 시장필터", "and": ["C03", "C18"]},
]

FINAL_SETS = [
    {"id": "S1", "name": "보수형",
     "and": ["C26", "C22", "C07"],
     "desc": "52주 신고가 돌파(이격도 15% 이내·거래량 1.5배) & 코스피>MA20 & 외국인 3일 연속 보유율↑ & 정배열 진입"},
    {"id": "S2", "name": "공격형",
     "and": ["C26"],
     "desc": "52주 신고가 돌파 & 코스피>MA20 (이격도 15% 이내, 거래량 20일평균 1.5배 이상)"},
]


def build_specs():
    base = cond_defs()
    base_fns = {c["id"]: c["fn"] for c in base}
    combos = [{"id": s["id"], "name": s["name"], "rule": f"AND={s.get('and', [])} OR={s.get('or', [])}",
               "type": "combo", "fn": compose(s, base_fns)} for s in COMBO_SPECS]
    finals = [{"id": s["id"], "name": f"{s['name']} 최종안", "rule": s["desc"], "type": "final",
               "fn": compose(s, base_fns)} for s in FINAL_SETS]
    return base, combos, finals


def run_slice(uni, idx, since_ts, verbose=True, cap_floor=MIN_MKTCAP):
    base, combos, finals = build_specs()
    specs = base + combos + finals
    export_ids = {s["id"] for s in finals}
    accs, used, names = run_pass(uni, idx, specs, export_ids, since_ts, verbose=verbose, cap_floor=cap_floor)
    return specs, accs, used, names


def write_outputs(specs, accs, used, names, args, meta_extra=None):
    rows = []
    for s in specs:
        m = summarize(accs[s["id"]])
        rows.append({"id": s["id"], "name": s["name"], "rule": s["rule"], "type": s["type"],
                     "reliable": m["signals"] >= MIN_SAMPLE, "metrics": m})

    def net20(r):
        h = r["metrics"]["h20"]
        return h["net"]["mean"] if h else -9

    ranked = sorted([r for r in rows if r["reliable"] and r["type"] not in ("final",)], key=lambda r: -net20(r))
    ids_top = [r["id"] for r in ranked[:5]]
    ov = []
    for i, a in enumerate(ids_top):
        for b in ids_top[i + 1:]:
            sa = {(d[0], d[1]) for d in accs[a].detail}
            sb = {(d[0], d[1]) for d in accs[b].detail}
            # detail은 최근 3개월만 있을 수 있으므로 신호 수 기준 겹침은 별도 계산 불가 → 비고
            inter = len(sa & sb)
            ov.append({"a": a, "b": b, "same_signal_recent": inter,
                       "pct_of_a_recent": inter / len(sa) if sa else 0,
                       "pct_of_b_recent": inter / len(sb) if sb else 0})

    payload = {
        "meta": {
            "generated": time.strftime("%Y-%m-%d %H:%M"),
            "period": [args.start, args.end],
            "tickers_used": used,
            "tickers_requested": meta_extra.get("requested") if meta_extra else None,
            "source": {"raw": "finance.daum.net chart API (adjusted=false) — 원주가·거래대금",
                       "adjusted": "api.finance.naver.com siseJson — 수정주가(액면분할·무상증자 반영, 배당락 미반영)",
                       "index": "Yahoo Finance ^KS11 / ^KQ11",
                       "universe": "m.stock.naver.com 종목목록 (현재 상장 종목만)"},
            "universe_rule": {"market": "KOSPI+KOSDAQ 보통주",
                              "min_mktcap": (meta_extra or {}).get("cap_floor", MIN_MKTCAP),
                              "min_turnover": MIN_VALUE, "max_disp20": MAX_DISP20},
            "cost": {"slippage_each_side": SLIP_BASE, "commission_each_side": COMM,
                     "tax_by_year": TAX_BY_YEAR, "slippage_grid": list(SLIPS)},
            "measure": {"entry": ENTRY_MODE, "exit": "보유 N거래일 종가",
                        "cooldown": COOLDOWN, "stops": list(STOPS), "min_sample": MIN_SAMPLE},
            "signal_export": {"finals": "전 기간 개별 신호", "others": f"최근 {RECENT_MONTHS}개월"},
        },
        "conditions": rows,
        "ranking_net20": [r["id"] for r in ranked],
        "overlap_recent": ov,
        "slippage_net20": {
            str(slip): {r["id"]: (r["metrics"]["h20"]["net"]["mean"] if r["metrics"]["h20"] else None) for r in rows}
            for slip in [SLIP_BASE]  # 기본값만; 민감도는 아래에서 별도 계산
        },
    }
    # 슬리피지 민감도(20일, 조건별)
    payload["slippage_net20"] = {}
    for slip in SLIPS:
        payload["slippage_net20"][str(slip)] = {
            s["id"]: (float(np.asarray(accs[s["id"]].rn[slip][20]).mean()) if len(accs[s["id"]].rn[slip][20]) else None)
            for s in specs
        }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    # 신호 기록
    signals = {s["id"]: [] for s in specs}
    for s in specs:
        for d in accs[s["id"]].detail:
            code, dt, edt, px, r5, r10, r20 = d
            name, mkt = names.get(code, ("", ""))
            signals[s["id"]].append([dt, code, name, mkt, edt, round(px, 2), r5, r10, r20])
    signals = {k: v for k, v in signals.items() if v}
    sig_payload = {
        "meta": {
            "version": 1,
            "generated": time.strftime("%Y-%m-%d %H:%M"),
            "period": [args.start, args.end],
            "note": "조건별 신호 기록. 컬럼= [신호일, 종목코드, 종목명, 시장, 매수일, 매수시가(수정주가), r5, r10, r20] "
                    "이며 r5/r10/r20은 슬리피지 0.10%·수수료 0.015%·거래세(연도별)를 뺀 순수익률(%). "
                    "청산 전(null) 신호는 이후 시세로 채우면 적중률이 갱신된다. 최종안은 전 기간, 나머지는 최근 "
                    f"{RECENT_MONTHS}개월치.",
            "conditions": [{"id": s["id"], "name": s["name"] if isinstance(s["name"], str) else str(s["name"])} for s in specs],
        },
        "signals": signals,
    }
    OUT_SIGNALS.write_text(json.dumps(sig_payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(pd.DataFrame([{"id": r["id"], "n": r["metrics"]["signals"],
                         "gross20": round(r["metrics"]["h20"]["gross"]["mean"] * 100, 2) if r["metrics"]["h20"] else None,
                         "net20": round(r["metrics"]["h20"]["net"]["mean"] * 100, 2) if r["metrics"]["h20"] else None,
                         "win20": round(r["metrics"]["h20"]["net"]["win"] * 100, 1) if r["metrics"]["h20"] else None}
                        for r in rows]).to_string(index=False), flush=True)
    print(f"[out] {OUT_JSON} / {OUT_SIGNALS}", flush=True)


def update_signals(start: str, end: str, workers: int = 6, refetch: bool = True) -> None:
    """저장된 신호 파일의 r5/r10/r20을 최신 시세로 다시 채운다(적중률 갱신)."""
    if not OUT_SIGNALS.exists():
        print("[update] 신호 파일이 없습니다. 먼저 --stage run 을 실행하세요.")
        return
    data = json.loads(OUT_SIGNALS.read_text(encoding="utf-8"))
    sigs = data.get("signals", {})
    codes = {rec[1] for recs in sigs.values() for rec in recs if rec[6] is None or rec[7] is None or rec[8] is None}
    if refetch and codes:
        print(f"[update] 시세 갱신 {len(codes)}종목", flush=True)
        stage_fetch(sorted(codes), start, end, workers, refresh=True)
    frames, updated, filled = {}, 0, 0
    for cid, recs in sigs.items():
        for rec in recs:
            code = rec[1]
            if code not in frames:
                p = CACHE / "ohlcv" / f"{code}.pkl"
                frames[code] = pd.read_pickle(p) if p.exists() else None
            df = frames[code]
            if df is None:
                continue
            try:
                t = df.index.get_loc(pd.Timestamp(str(rec[0])))
            except KeyError:
                continue
            entry = rec[5]
            out = []
            for h in HORIZONS:
                k = t + h
                if k >= len(df) or not np.isfinite(df["aclose"].iloc[k]):
                    out.append(None)
                    continue
                tax = TAX_BY_YEAR.get(df.index[k].year, 0.0020)
                buy_f = (1 + SLIP_BASE) * (1 + COMM)
                sell_f = (1 - SLIP_BASE) * (1 - COMM - tax)
                out.append(round((df["aclose"].iloc[k] * sell_f / (entry * buy_f) - 1) * 100, 2))
            if rec[6:9] != out:
                if any(v is not None for v in out) and any(v is None for v in rec[6:9]):
                    filled += 1
                updated += 1
                rec[6], rec[7], rec[8] = out
    data["meta"]["updated"] = time.strftime("%Y-%m-%d %H:%M")
    OUT_SIGNALS.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[update] {updated}건 갱신, 새로 채워진 신호 {filled}건", flush=True)
    for cid, recs in sigs.items():
        v20 = np.array([r[8] for r in recs if r[8] is not None], dtype=float)
        if len(v20):
            print(f"  {cid}: 완료 {len(v20)}/{len(recs)}  20일 평균 {v20.mean():+.2f}%  승률 {(v20>0).mean()*100:.1f}%", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all", choices=["all", "fetch", "run", "merge", "update-signals"])
    ap.add_argument("--start", default=START)
    ap.add_argument("--end", default=END)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--shard", type=int, default=-1)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--cap-floor", type=float, default=float(MIN_MKTCAP), help="시총 하한(원)")
    ap.add_argument("--out", default="", help="결과 JSON 경로(기본 data/backtest.json, 신호파일은 자동)")
    args = ap.parse_args()
    if args.out:
        globals()["OUT_JSON"] = Path(args.out)

    uni = prepare_universe()
    print(f"[universe] 코스피+코스닥 보통주 {len(uni)}종목", flush=True)

    if args.stage in ("all", "fetch"):
        fetch_index()
        sel = uni["code"].tolist()[args.offset: args.offset + args.limit if args.limit else None]
        stage_fetch(sel, args.start, args.end, args.workers, args.refresh)
        if args.stage == "fetch":
            return

    if args.stage == "update-signals":
        update_signals(args.start, args.end, args.workers)
        return

    idx = fetch_index()
    since_ts = pd.Timestamp(args.end) - pd.DateOffset(months=RECENT_MONTHS)

    if args.stage == "merge":
        files = sorted((CACHE / "shards").glob("acc_*.pkl"))
        base, combos, finals = build_specs()
        specs = base + combos + finals
        accs = {s["id"]: Acc() for s in specs}
        used = 0
        names = {}
        for f in files:
            part = pickle.loads(f.read_bytes())
            used += part["used"]
            names.update(part["names"])
            for s in specs:
                accs[s["id"]].merge(Acc.load(part["accs"][s["id"]]))
        print(f"[merge] shards {len(files)}, used {used}", flush=True)
        write_outputs(specs, accs, used, names, args, {"requested": int(len(uni)), "cap_floor": args.cap_floor})
        return

    run_uni = uni.iloc[args.offset: args.offset + args.limit if args.limit else None]
    specs, accs, used, names = run_slice(run_uni, idx, since_ts, cap_floor=args.cap_floor)

    if args.shard >= 0:
        data = {"used": used, "names": names, "accs": {s["id"]: accs[s["id"]].dump() for s in specs},
                "requested": int(len(run_uni)), "cap_floor": args.cap_floor}
        p = CACHE / "shards" / f"acc_{args.shard:02d}.pkl"
        p.write_bytes(pickle.dumps(data, protocol=4))
        print(f"[shard] {p} (used {used})", flush=True)
        return

    write_outputs(specs, accs, used, names, args, {"requested": int(len(run_uni)), "cap_floor": args.cap_floor})


if __name__ == "__main__":
    main()
