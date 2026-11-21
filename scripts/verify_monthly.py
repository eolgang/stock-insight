#!/usr/bin/env python3
"""표본 검증: 저장된 월별 특징값·수익률을 원자료에서 독립적으로 손계산해 대조한다.

collect 파이프라인의 prep()를 쓰지 않고, pandas 원시 연산만으로 다시 계산한다.
"""
import numpy as np
import pandas as pd
from pathlib import Path

from monthly_surge import CACHE, WORK, SLIP, COMM, TAX_BY_YEAR

OHLCV = CACHE / "ohlcv"
m = pd.read_pickle(WORK / "surge_monthly.pkl")
m["ev"] = m["ret"] >= 0.20

# 사건(20% 이상) 중 서로 다른 시기·규모·시장에서 5건
CASES = []
sel = m[m.ev].reset_index(drop=True)
# 큰 사건·중간 사건·작은 사건을 고르게: 수익률 상위/중간/하위 + 종목 중복 없이
idxs = [0, 300, 900, 1800, 2700, len(sel) - 1]
for i in idxs:
    r = sel.iloc[min(i, len(sel) - 1)]
    if r.code not in {c.code for c in CASES}:
        CASES.append(r)


def recompute(code, mlabel):
    d = pd.read_pickle(OHLCV / f"{code}.pkl")
    c = d["aclose"].astype(float)
    h = d["ahigh"].astype(float)
    o = d["aopen"].astype(float)
    v = d["volume"].astype(float)
    days = d.index
    per = days.to_period("M")
    # 그 달 마지막 거래일 / 전월 마지막 거래일
    M_end = np.flatnonzero(np.asarray(per) == pd.Period(mlabel, "M"))[-1]
    prev_period = pd.Period(mlabel, "M") - 1
    T_end = np.flatnonzero(np.asarray(per) == prev_period)[-1]
    ret = c.iloc[M_end] / c.iloc[T_end] - 1
    T = T_end
    ma = {n: c.iloc[T - n + 1:T + 1].mean() for n in (5, 20, 60, 120, 240)}
    disp20 = c.iloc[T] / ma[20] - 1
    align3 = float(ma[5] > ma[20] > ma[60])
    # 10일 내 60일 고점 돌파 (그날 종가 > 직전 60일 고가)
    flags = {}
    for k in range(0, 11):
        p_ = T - k
        flags[k] = bool(c.iloc[p_] > h.iloc[p_ - 60:p_].max())
    xh60 = float(flags[0])
    ds60h = next((k for k in range(0, 11) if flags[k]), 9999)
    vmax10 = max(v.iloc[j] / v.iloc[j - 19:j + 1].mean() for j in range(T - 9, T + 1))
    # 볼린저폭 백분위
    sd = c.rolling(20).std(ddof=0)
    bbw = 4 * sd / c.rolling(20).mean()
    w = bbw.iloc[T - 249:T + 1]
    bbwpct = (w < bbw.iloc[T]).sum() / len(w)
    return dict(ret=ret, ma20=ma[20], ma60=ma[60], disp20=disp20, align3=align3,
                xh60=xh60, ds60h=ds60h, vmax10=vmax10, bbwpct=bbwpct)


rows = []
for _, r in pd.DataFrame(CASES).iterrows():
    got = recompute(r.code, r.M)
    chk = [("월 수익률", r.ret, got["ret"], 1e-12), ("MA20", np.nan, got["ma20"], 0),
           ("MA60", np.nan, got["ma60"], 0), ("MA20 이격도", r.disp20, got["disp20"], 1e-12),
           ("정배열", r.align3, got["align3"], 1e-12),
           ("당일 60일고점 돌파", r.xh60, got["xh60"], 1e-12),
           ("60일고점 돌파 경과일", r.dsh60, got["ds60h"], 1e-12),
           ("최대거래량배수", r.vmax10, got["vmax10"], 1e-9),
           ("BB폭백분위", r.bbwpct, got["bbwpct"], 0.02)]
    for name, a, b, tol in chk:
        if name in ("MA20", "MA60"):
            rows.append([r["name"], r.M, name, "—", f"{b:,.0f}", "계산값"])
            continue
        if name == "60일고점 돌파 경과일" and float(a) > 10 and float(b) == 9999:
            rows.append([r["name"], r.M, name, f"{a:.0f}일 전", "10일 안엔 없음", "일치(10일 밖)"])
            continue
        diff = abs(float(a) - float(b))
        ok = "0 (일치)" if diff <= max(tol, 1e-12) else f"차이 {diff:.2e}"
        if isinstance(a, float) and abs(a) < 10:
            rows.append([r["name"], r.M, name, f"{a:.6f}", f"{b:.6f}", ok])
        else:
            rows.append([r["name"], r.M, name, f"{a:,.2f}", f"{b:,.2f}", ok])

out = ["| 종목 | 급등한 달 | 항목 | 저장값(파이프라인) | 독립 재계산 | 판정 |",
       "|---|---|---|---:|---:|---|"]
for x in rows:
    out.append("| " + " | ".join(str(v) for v in x) + " |")
Path(WORK / "report_parts").mkdir(exist_ok=True)
(WORK / "report_parts" / "verify.md").write_text("\n".join(out) + "\n", encoding="utf-8")
print("\n".join(out))

# 눌림목 진입 수익률 1건 손검증
print("\n[눌림목 검증]")
d = pd.read_pickle(OHLCV / "035420.pkl")
c = d["aclose"].astype(float)
o = d["aopen"].astype(float)
ma20 = c.rolling(20).mean()
last = None
for i in range(300, len(d) - 25):
    if c.iloc[i - 1] <= ma20.iloc[i - 1] and c.iloc[i] > ma20.iloc[i]:
        for j in range(1, 11):
            k = i + j
            rel = c.iloc[k] / ma20.iloc[k] - 1
            if abs(rel) <= 0.02 and (c.iloc[k] > o.iloc[k] or c.iloc[k] > c.iloc[k - 1]):
                last = (i, k)
                break
        if last:
            break
i, k = last
cost = 2 * SLIP + 2 * COMM + TAX_BY_YEAR.get(int(d.index[k + 1].year), 0.0020)
r20 = c.iloc[k + 1 + 19] / o.iloc[k + 1] - 1 - cost
print(f"NAVER {d.index[i].date()} MA20 돌파 → {d.index[k].date()} 눌림 반등 → "
      f"{d.index[k+1].date()} 시가 진입 → 20거래일 뒤 종가 {d.index[k+20].date()}")
print(f"  종가 {c.iloc[k+1+19]:,.0f} / 시가 {o.iloc[k+1]:,.0f} - 1 - 비용 {cost*100:.3f}% = {r20*100:+.2f}%")
