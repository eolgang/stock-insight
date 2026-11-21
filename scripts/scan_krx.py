#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
얼미리서치 · 장마감 조건검색 스캐너 (scan_krx.py)
================================================
확정(장마감) 데이터만 쓴다. 장중 실시간 값은 쓰지 않는다.

데이터 소스 (2026-11-21 이 환경에서 실제 동작 확인)
  1) 유니버스·시가총액·거래대금 : m.stock.naver.com / api/stocks/marketValue/{KOSPI|KOSDAQ}
  2) 일별 OHLCV(수정주가)       : fchart.stock.naver.com / sise.nhn (EUC-KR XML, 최대 1,200봉)
  3) 외국인·기관 순매수         : m.stock.naver.com / api/stock/{code}/trend
  * data.krx.co.kr 전종목 bld 응답은 이 환경에서 빈 응답 → 미사용
  * 표준 라이브러리만 사용(외부 패키지 설치 불필요)

출력
  data/screener.json          오늘의 조건검색 결과
  data/screener-history.json  날짜별 히트 누적 + 1거래일/5거래일 뒤 성과
  data/ma-charts.json         종목 차트용 일봉 + 이동평균(5·20·60·120·240·360·480·720)

사용
  python3 scripts/scan_krx.py                     # 오늘(직전 거래일) 스캔
  python3 scripts/scan_krx.py --seed 10           # 최근 10거래일 소급 계산(no look-ahead)
  python3 scripts/scan_krx.py --as-of 20261113    # 특정일 기준으로 스캔
"""

import argparse
import bisect
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

MIN_MARKET_CAP = 50_000_000_000     # 시가총액 500억 원 미만 제외
MIN_TRADE_VALUE = 10_000_000_000    # 거래대금 100억 원 미만 제외
HOT_GAP_LIMIT = 15.0                # 20일선 이격도 +15% 초과는 공통 제외(과열)
MAX_ITEMS = 20                      # 프리셋당 최대 종목
HISTORY_DAYS = 60                   # 히스토리 보관 거래일 수
CHART_SESSIONS = 90                 # 차트에 담을 최근 거래일 수
OHLCV_COUNT = 900                   # 일봉 수집 개수(720일선까지 그리려면 800+ 필요)
MA_PERIODS = (5, 20, 60, 120, 240, 360, 480, 720)
REF_TICKER = "005930"               # 거래일 달력 기준
EXTRA_CHART_TICKERS = ("005930", "000660", "267260")   # 실시간 차트 탭 KRX 버튼용


# ---------------------------------------------------------------- http
def _fetch(url, headers, timeout=25, tries=4, encoding=None):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            return raw.decode(encoding, "ignore") if encoding else raw.decode("utf-8", "ignore")
        except Exception as e:                       # noqa: BLE001
            last = e
            if i < tries - 1:
                time.sleep(0.6 * (i + 1))
    raise last


def get_json(url):
    txt = _fetch(url, {"User-Agent": UA_MOBILE, "Referer": "https://m.stock.naver.com/",
                       "Accept": "application/json, text/plain, */*"})
    return json.loads(txt)


# ---------------------------------------------------------------- universe
PREF_RE = re.compile(r"(\d*우B?C?|우\(전환\))$")


def fetch_listing(market):
    out, page = [], 1
    while page <= 40:
        data = get_json("https://m.stock.naver.com/api/stocks/marketValue/%s?pageSize=100&page=%d"
                        % (market, page))
        stocks = data.get("stocks") or []
        if not stocks:
            break
        stop = False
        for st in stocks:
            cap = float(st.get("marketValueRaw") or 0)
            if cap and cap < MIN_MARKET_CAP:
                stop = True                          # 시총 내림차순 정렬
                break
            out.append(st)
        if stop or len(stocks) < 100:
            break
        page += 1
    return out


def build_universe():
    universe = []
    dropped = {"etf_etn": 0, "preferred": 0, "spac": 0, "halted": 0, "illiquid": 0}
    for market in ("KOSPI", "KOSDAQ"):
        for st in fetch_listing(market):
            name = st.get("stockName") or ""
            if st.get("stockEndType") != "stock":
                dropped["etf_etn"] += 1
                continue
            if PREF_RE.search(name):
                dropped["preferred"] += 1
                continue
            if "스팩" in name:
                dropped["spac"] += 1
                continue
            if st.get("tradableStatus") not in (None, "tradable"):
                dropped["halted"] += 1
                continue
            value = float(st.get("accumulatedTradingValueRaw") or 0)
            if value < MIN_TRADE_VALUE:
                dropped["illiquid"] += 1
                continue
            universe.append({
                "code": st["itemCode"], "name": name, "market": market,
                "cap": float(st.get("marketValueRaw") or 0), "value": value,
            })
    return universe, dropped


# ---------------------------------------------------------------- per-stock
def fetch_daily(code):
    txt = _fetch("https://fchart.stock.naver.com/sise.nhn?symbol=%s&timeframe=day&count=%d"
                 "&requestType=0" % (code, OHLCV_COUNT),
                 {"User-Agent": UA_PC, "Referer": "https://finance.naver.com/"}, encoding="euc-kr")
    rows = []
    for node in ET.fromstring(txt).iter(tag="item"):
        p = (node.get("data") or "").split("|")
        if len(p) < 6:
            continue
        try:
            rows.append({"d": p[0], "o": float(p[1]), "h": float(p[2]),
                         "l": float(p[3]), "c": float(p[4]), "v": float(p[5])})
        except ValueError:
            continue
    rows.sort(key=lambda r: r["d"])
    return rows


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return 0.0


def fetch_flow(code, days=60):
    data = get_json("https://m.stock.naver.com/api/stock/%s/trend?pageSize=%d" % (code, days))
    return [{"d": str(r.get("bizdate") or ""), "f": _num(r.get("foreignerPureBuyQuant")),
             "i": _num(r.get("organPureBuyQuant"))} for r in (data or [])]


# ---------------------------------------------------------------- helpers
def mean(xs):
    return sum(xs) / len(xs) if xs else None


def streak(flows, key, upto):
    seq = sorted([f for f in flows if f["d"] <= upto], key=lambda f: f["d"], reverse=True)
    n, total = 0, 0.0
    for f in seq:
        if f[key] > 0:
            n += 1
            total += f[key]
        else:
            break
    return n, total


def slice_upto(series, date_raw):
    hi = bisect.bisect_right([r["d"] for r in series], date_raw)
    return series[:hi]


# ---------------------------------------------------------------- metrics
def build_metrics(stock, series, flows, date_raw):
    series = slice_upto(series, date_raw)
    closes = [r["c"] for r in series]
    highs = [r["h"] for r in series]
    vols = [r["v"] for r in series]
    if len(closes) < 21:
        return None
    c, prev = closes[-1], closes[-2]
    ma = {n: (mean(closes[-n:]) if len(closes) >= n else None) for n in (5, 20, 60)}
    if not ma[20]:
        return None
    ma20_prev = mean(closes[-21:-1])
    high52 = max(highs[-250:])                     # 52주 최고가는 장중 고가 기준(네이버·HTS 표기와 동일)
    prev_high52 = max(highs[-250:-1])
    vol_avg = mean(vols[-21:-1])
    vm = (vols[-1] / vol_avg) if vol_avg else 0.0
    f3, f3s = streak(flows, "f", date_raw)
    i3, i3s = streak(flows, "i", date_raw)
    return {
        "code": stock["code"], "name": stock["name"], "market": stock["market"],
        "close": int(round(c)),
        "chgPct": round((c / prev - 1) * 100, 2) if prev else 0.0,
        "ma20": int(round(ma[20])), "ma20Gap": round((c / ma[20] - 1) * 100, 2),
        "ma5": int(round(ma[5])) if ma[5] else None,
        "ma60": int(round(ma[60])) if ma[60] else None,
        "ma20Prev": int(round(ma20_prev)) if ma20_prev else None,
        "volMult": round(vm, 2),
        "from52wHigh": round((c / high52 - 1) * 100, 2),
        "new52wHigh": len(highs) > 1 and highs[-1] >= prev_high52,
        "vol3": sum(vols[-3:]),
        "foreign3d": f3, "foreign3dSum": int(f3s),
        "inst3d": i3, "inst3dSum": int(i3s),
        "value": int(stock["value"]), "cap": int(stock["cap"]),
    }


PRESETS = [
    {"id": "ma20-breakout", "name": "20일선 돌파",
     "desc": "전일에는 20일선 아래였는데 오늘 20일선을 뚫고 올라온 종목. 거래량이 20일 평균의 2배 이상.",
     "rules": ["전일 종가 < 전일 MA20", "당일 종가 > 당일 MA20",
               "당일 거래량 ≥ 20일 평균 거래량 × 2", "20일선 이격도 +15% 초과 제외"]},
    {"id": "high52-breakout", "name": "52주 신고가",
     "desc": "52주 최고가의 -2% 안쪽이거나 오늘 장중 새로 갱신. 거래량 1.5배 이상 함께 터진 종목.",
     "rules": ["종가 ≥ 52주 최고가(장중 고가) × 0.98 또는 장중 신고가 갱신", "당일 거래량 ≥ 20일 평균 거래량 × 1.5",
               "20일선 이격도 +15% 초과 제외"]},
    {"id": "supply-catch", "name": "수급 포착",
     "desc": "외국인 또는 기관이 3거래일 연속 순매수하면서 종가가 20일선 위에 있는 종목.",
     "rules": ["외국인 3거래일 연속 순매수 또는 기관 3거래일 연속 순매수",
               "당일 종가 > MA20", "20일선 이격도 +15% 초과 제외"]},
    {"id": "pullback", "name": "눌림목",
     "desc": "5·20·60일선 정배열에 20일선 이격도 +2~+10% 구간, 거래량 1.2배 이상.",
     "rules": ["MA5 > MA20 > MA60", "20일선 이격도 +2% ~ +10%",
               "당일 거래량 ≥ 20일 평균 거래량 × 1.2", "20일선 이격도 +15% 초과 제외"]},
]


def match_preset(pid, m):
    vm = m["volMult"] or 0
    if pid == "ma20-breakout":
        if not m["ma20Prev"]:
            return None
        if not (m["close"] < m["ma20Prev"] and m["close"] > m["ma20"]) or vm < 2.0:
            return None
        return {"score": vm + max(0.0, m["ma20Gap"]), "signal": "20일선 돌파 · 거래량 %.1f배" % vm}
    if pid == "high52-breakout":
        if m["from52wHigh"] < -2.0 or vm < 1.5:
            return None
        sig = "52주 신고가" if m["new52wHigh"] else "신고가 %+.1f%%" % m["from52wHigh"]
        return {"score": vm + (m["from52wHigh"] + 2.0), "signal": sig}
    if pid == "supply-catch":
        if m["close"] <= m["ma20"] or (m["foreign3d"] < 3 and m["inst3d"] < 3):
            return None
        who = []
        if m["foreign3d"] >= 3:
            who.append("외인 %d일" % m["foreign3d"])
        if m["inst3d"] >= 3:
            who.append("기관 %d일" % m["inst3d"])
        # 최근 3거래일 거래량 대비 순매수 강도 + 갓 시작한 streak를 우선
        strong = max(m["foreign3dSum"] if m["foreign3d"] >= m["inst3d"] else m["inst3dSum"], 0)
        intensity = (strong / m["vol3"] * 100) if m.get("vol3") else 0.0
        fresh = 10.0 - min(max(m["foreign3d"], m["inst3d"]), 10)
        return {"score": intensity * 2 + fresh * 0.1,
                "signal": " · ".join(who) + " 연속 순매수"}
    if pid == "pullback":
        if not (m["ma5"] and m["ma60"]) or not (m["ma5"] > m["ma20"] > m["ma60"]):
            return None
        if not (2.0 <= m["ma20Gap"] <= 10.0) or vm < 1.2:
            return None
        return {"score": vm + (10.0 - abs(m["ma20Gap"] - 5.0)) / 5.0,
                "signal": "정배열 · 이격 %+.1f%%" % m["ma20Gap"]}
    return None


def public_item(m, sig):
    return {"code": m["code"], "name": m["name"], "market": m["market"],
            "close": m["close"], "chgPct": m["chgPct"], "ma20": m["ma20"],
            "ma20Gap": m["ma20Gap"], "volMult": m["volMult"],
            "from52wHigh": m["from52wHigh"], "value": m["value"],
            "foreign3d": m["foreign3d"], "foreign3dSum": m["foreign3dSum"],
            "inst3d": m["inst3d"], "inst3dSum": m["inst3dSum"],
            "signal": sig["signal"], "score": round(sig["score"], 2)}


def scan_day(metrics, date_raw):
    """metrics: 종목별 전체 시계열·수급 캐시 → 해당일 지표·프리셋 결과."""
    hot = 0
    day_metrics = []
    for stock, series, flows in metrics:
        m = build_metrics(stock, series, flows, date_raw)
        if not m:
            continue
        if m["ma20Gap"] > HOT_GAP_LIMIT:
            hot += 1
            continue
        day_metrics.append(m)
    presets = []
    hits = {}
    for p in PRESETS:
        rows = []
        for m in day_metrics:
            sig = match_preset(p["id"], m)
            if sig:
                rows.append(public_item(m, sig))
        rows.sort(key=lambda r: r["score"], reverse=True)
        rows = rows[:MAX_ITEMS]
        hits[p["id"]] = rows
        presets.append({"id": p["id"], "name": p["name"], "desc": p["desc"],
                        "rules": p["rules"], "count": len(rows), "items": rows})
    return {"universe": len(day_metrics), "hot": hot, "presets": presets, "hits": hits}


# ---------------------------------------------------------------- charts
def ma_series(closes, n):
    out, s = [], 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= n:
            s -= closes[i - n]
        out.append(round(s / n, 1) if i >= n - 1 else None)
    return out


def chart_payload(series, name, market):
    closes = [r["c"] for r in series]
    tail = series[-CHART_SESSIONS:]
    n = len(tail)
    mas = {}
    for p in MA_PERIODS:
        arr = ma_series(closes, p)[-n:]
        mas[str(p)] = arr if arr and arr[0] is not None else None
    return {"name": name, "market": market,
            "d": [r["d"][4:6] + "/" + r["d"][6:8] for r in tail],
            "c": [int(round(r["c"])) for r in tail],
            "v": [int(r["v"]) for r in tail],
            "ma": mas,
            "bars": len(closes),
            "baseDate": "%s-%s-%s" % (tail[-1]["d"][:4], tail[-1]["d"][4:6], tail[-1]["d"][6:8])}


# ---------------------------------------------------------------- history
def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                # noqa: BLE001
        return default


HORIZONS = ((1, "r1"), (5, "r5"), (10, "r10"), (20, "r20"))


def fill_returns(hist, ref_dates, series_by_code):
    """히트 후 1·5·10·20거래일 뒤 종가 기준 등락률을 채운다(확정 데이터만)."""
    idx = {"%s-%s-%s" % (d[:4], d[4:6], d[6:8]): i for i, d in enumerate(ref_dates)}
    for day in hist["days"]:
        d = day["date"]
        if d not in idx:
            continue
        i0 = idx[d]
        for hits in day["presets"].values():
            for h in hits:
                if all(h.get(k) is not None for _, k in HORIZONS):
                    continue
                series = series_by_code.get(h["c"])
                if not series:
                    continue
                bydate = {r["d"]: r["c"] for r in series}
                entry = h.get("e") or bydate.get(d.replace("-", ""))
                if not entry:
                    continue
                for off, key in HORIZONS:
                    if h.get(key) is not None:
                        continue
                    if i0 + off < len(ref_dates):
                        px = bydate.get(ref_dates[i0 + off])
                        if px:
                            h[key] = round((px / entry - 1) * 100, 2)


def compute_stats(hist):
    stats = {}
    for p in PRESETS:
        pid = p["id"]
        buckets = {k: [] for _, k in HORIZONS}
        total = 0
        for day in hist["days"]:
            for h in day["presets"].get(pid, []):
                total += 1
                for _, k in HORIZONS:
                    if h.get(k) is not None:
                        buckets[k].append(h[k])
        row = {"hits": total}
        for _, k in HORIZONS:
            vals = buckets[k]
            row["done" + k[1:]] = len(vals)
            row["avg" + k[1:]] = round(mean(vals), 2) if vals else None
            row["win" + k[1:]] = (round(sum(1 for x in vals if x > 0) / len(vals) * 100, 1)
                                  if vals else None)
        stats[pid] = row
    return stats


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--as-of", help="기준일 YYYYMMDD (기본: 최근 거래일)")
    ap.add_argument("--seed", type=int, default=0, help="최근 N거래일을 소급 계산해 히스토리 채우기")
    ap.add_argument("--no-history", action="store_true")
    args = ap.parse_args()
    log = (lambda *a: None) if args.quiet else (lambda *a: print(*a, file=sys.stderr))

    t0 = time.time()
    ref = fetch_daily(REF_TICKER)
    if len(ref) < 260:
        raise SystemExit("기준 종목 일봉이 부족합니다 (%d봉)" % len(ref))
    ref_dates = [r["d"] for r in ref]
    base_raw = (args.as_of or ref_dates[-1]).replace("-", "")
    if base_raw not in ref_dates:
        raise SystemExit("기준일 %s 는 거래일이 아닙니다" % base_raw)
    log("기준일 %s · 거래일 %d봉 · %s" % (base_raw, len(ref_dates),
                                       "%s~%s" % (ref_dates[0], ref_dates[-1])))

    universe, dropped = build_universe()
    log("유니버스 %d종목 (제외 %s)" % (len(universe), dropped))

    by_code = {}
    def work(stock):
        try:
            return stock, fetch_daily(stock["code"]), fetch_flow(stock["code"])
        except Exception as e:                       # noqa: BLE001
            log("  ! %s %s: %s" % (stock["code"], stock["name"], e))
            return stock, None, None

    with ThreadPoolExecutor(max_workers=6) as ex:
        for stock, series, flows in ex.map(work, universe):
            if series:
                by_code[stock["code"]] = (stock, series, flows or [])
    log("일봉·수급 수집 %d종목 (%.1fs)" % (len(by_code), time.time() - t0))

    metrics = list(by_code.values())
    result = scan_day(metrics, base_raw)
    for p in result["presets"]:
        log("  %-14s %d종목" % (p["id"], p["count"]))

    # 종목 차트(프리셋 히트 + 실시간 차트 탭 KRX 기준 종목)
    charts = {}
    codes = []
    for rows in result["hits"].values():
        codes.extend(r["code"] for r in rows)
    codes.extend(EXTRA_CHART_TICKERS)
    for code in dict.fromkeys(codes):
        if code in by_code:
            stock, series, _ = by_code[code]
            charts[code] = chart_payload(series, stock["name"], stock["market"])
        else:
            try:
                series = fetch_daily(code)
                charts[code] = chart_payload(series, code, "")
            except Exception:                        # noqa: BLE001
                pass

    os.makedirs(args.out_dir, exist_ok=True)
    now = datetime.now(KST)
    screener = {
        "updated": now.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
        "baseDate": "%s-%s-%s" % (base_raw[:4], base_raw[4:6], base_raw[6:8]),
        "universe": result["universe"], "universeRaw": len(universe),
        "hotFiltered": result["hot"],
        "source": "네이버 금융 장마감 확정 데이터(일봉·시가총액·거래대금·투자자별 순매수)",
        "priceBasis": "수정주가(네이버 일봉, 액면분할·무상증자 반영)",
        "filters": {"minCap": MIN_MARKET_CAP, "minValue": MIN_TRADE_VALUE,
                    "hotGapLimit": HOT_GAP_LIMIT, "excluded": dropped},
        "maPeriods": list(MA_PERIODS),
        "charts": list(charts.keys()),
        "presets": result["presets"],
    }
    with open(os.path.join(args.out_dir, "screener.json"), "w", encoding="utf-8") as f:
        json.dump(screener, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(args.out_dir, "ma-charts.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": screener["updated"], "baseDate": screener["baseDate"],
                   "maPeriods": list(MA_PERIODS), "charts": charts},
                  f, ensure_ascii=False, separators=(",", ":"))

    if args.no_history:
        log("완료 %.1fs (히스토리 생략)" % (time.time() - t0))
        return

    hpath = os.path.join(args.out_dir, "screener-history.json")
    hist = load_json(hpath, {"days": []})
    if "days" not in hist:
        hist["days"] = []

    # 채울 날짜: 소급(--seed) + 기준일
    targets = [base_raw]
    if args.seed > 0:
        upto = [d for d in ref_dates if d <= base_raw]
        targets = upto[-args.seed:]
        if base_raw not in targets:
            targets.append(base_raw)
    for date_raw in targets:
        day = scan_day(metrics, date_raw)
        ds = "%s-%s-%s" % (date_raw[:4], date_raw[4:6], date_raw[6:8])
        hist["days"] = [d for d in hist["days"] if d["date"] != ds]
        entry = {"date": ds, "presets": {pid: [{"c": r["code"], "n": r["name"], "e": r["close"]}
                                                 for r in rows]
                                          for pid, rows in day["hits"].items()}}
        if date_raw != base_raw:
            entry["seeded"] = True
        hist["days"].append(entry)

    series_by_code = {code: v[1] for code, v in by_code.items()}
    # 유니버스에서 빠진 종목의 히트도 성과를 채울 수 있게 개별 조회
    for day in hist["days"]:
        for hits in day["presets"].values():
            for h in hits:
                if h["c"] not in series_by_code and (h.get("r1") is None or h.get("r5") is None):
                    try:
                        series_by_code[h["c"]] = fetch_daily(h["c"])
                    except Exception:                # noqa: BLE001
                        series_by_code[h["c"]] = None

    fill_returns(hist, ref_dates, series_by_code)
    keep = set(ref_dates[-HISTORY_DAYS:])
    hist["days"] = sorted([d for d in hist["days"]
                           if d["date"].replace("-", "") in keep], key=lambda d: d["date"])
    hist.update({"updated": screener["updated"], "baseDate": screener["baseDate"],
                 "stats": compute_stats(hist),
                 "presetNames": {p["id"]: p["name"] for p in PRESETS}})
    with open(hpath, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, separators=(",", ":"))
    log("히스토리 %d거래일 누적 · 완료 %.1fs" % (len(hist["days"]), time.time() - t0))


if __name__ == "__main__":
    main()
