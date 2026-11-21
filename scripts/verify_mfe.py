#!/usr/bin/env python3
"""mfe_analysis.py 표본 검증 — 저장된 신호 5건을 원자료에서 손으로 재계산해 대조한다.

원자료(work/backtest/cache/ohlcv/{code}.pkl)에서 이평선·신호·진입가·MFE·비용을
벡터화 없이 순수 파이썬 루프로 다시 계산하고, mfe_signals.npz의 값과 비교한다.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
WORK = REPO.parent / "work" / "backtest"
CACHE = WORK / "cache"
TAX = {2015: 0.0030, 2016: 0.0030, 2017: 0.0030, 2018: 0.0030, 2019: 0.0030,
       2020: 0.0025, 2021: 0.0023, 2022: 0.0023, 2023: 0.0020, 2024: 0.0018,
       2025: 0.0015, 2026: 0.0020}
WINDOWS = (1, 2, 3, 5, 10, 20)

z = np.load(WORK / "mfe_signals.npz")
uni = pd.read_pickle(CACHE / "universe.pkl").set_index("code")

random.seed(20261121)
picks = []
for key in ("A21", "B21", "A21", "A61", "B61"):
    n = z[f"{key}__sd"].shape[0]
    i = random.randrange(n)
    picks.append((key, i))

lines = ["# 표본 검증 (원자료 수동 재계산)", ""]
ok_all = True
for key, i in picks:
    code = str(z[f"{key}__code"][i])
    sd = int(z[f"{key}__sd"][i])
    entry_npz = float(z[f"{key}__entry"][i])
    mfc_npz = z[f"{key}__mfc"][i]
    mfh_npz = z[f"{key}__mfh"][i]
    am_npz = int(z[f"{key}__argmax"][i])
    cost_npz = z[f"{key}__cost"][i]

    df = pd.read_pickle(CACHE / "ohlcv" / f"{code}.pkl")
    dates = [d.strftime("%Y%m%d") for d in df.index]
    si = dates.index(str(sd))
    N = 21 if key.endswith("21") else 61

    ac = [float(x) for x in df["aclose"].to_numpy()]
    ao = [float(x) for x in df["aopen"].to_numpy()]
    ah = [float(x) for x in df["ahigh"].to_numpy()]
    vol = [float(x) for x in df["volume"].to_numpy()]
    tv = [float(x) for x in df["turnover"].to_numpy()]

    ma_now = sum(ac[si - N + 1:si + 1]) / N
    ma_prev = sum(ac[si - N:si]) / N
    cond_a = ac[si] > ma_now and ac[si - 1] <= ma_prev
    cond_b = ao[si] <= ma_now
    nm = uni.loc[code, "name"]
    shares = float(uni.loc[code, "mktcap_eok"]) * 1e8 / float(uni.loc[code, "last_close"])
    mktcap = ac[si] * shares
    tvma20 = sum(tv[si - 19:si + 1]) / 20
    ma20 = sum(ac[si - 19:si + 1]) / 20
    disp = ac[si] / ma20 - 1

    if key.startswith("A"):
        uni_ok = cond_a
    else:
        uni_ok = cond_a and cond_b
    uni_ok = uni_ok and mktcap >= 5e10 and vol[si] > 0 and tvma20 >= 1e10 and disp <= 0.15

    e = si + 1
    entry = ao[e]
    rc = [ac[e + k] / entry - 1 for k in range(20)]
    rh = [ah[e + k] / entry - 1 for k in range(20)]
    mfc = [max(rc[:w]) for w in WINDOWS]
    mfh = [max(rh[:w]) for w in WINDOWS]
    am = rc.index(max(rc)) + 1
    cost = [2 * 0.001 + 2 * 0.00015 + TAX[int(df.index[e + w - 1].year)] for w in WINDOWS]

    d_mfc = max(abs(mfc[k] - mfc_npz[k]) for k in range(len(WINDOWS)))
    d_mfh = max(abs(mfh[k] - mfh_npz[k]) for k in range(len(WINDOWS)))
    good = (abs(entry - entry_npz) < 0.01) and d_mfc < 1e-9 and d_mfh < 1e-9 and am == am_npz and uni_ok
    ok_all &= bool(good)

    lines += [f"## {key} — {code} {nm} (신호일 {sd})", "",
              f"- 신호일 종가 {ac[si]:,.0f} / MA{N} {ma_now:,.1f} / 전일 종가 {ac[si-1]:,.0f} / 전일 MA{N} {ma_prev:,.1f} → 종가돌파 {cond_a}"
              + (f", 시가 {ao[si]:,.0f} ≤ MA{N} → 몸통돌파 {cond_b}" if key.startswith("B") else ""),
              f"- 유니버스: 시총 {mktcap/1e8:,.0f}억, 20일 평균 거래대금 {tvma20/1e8:,.1f}억, 20일선 이격 {disp*100:+.1f}% → 통과 {uni_ok}",
              f"- 진입: 다음 거래일({df.index[e].strftime('%Y-%m-%d')}) 시가 {entry:,.0f} (npz {entry_npz:,.0f})",
              f"- MFE(종가) 창별: " + " / ".join(f"{w}일 {mfc[k]*100:+.2f}%" for k, w in enumerate(WINDOWS)),
              f"- MFE(고가) 창별: " + " / ".join(f"{w}일 {mfh[k]*100:+.2f}%" for k, w in enumerate(WINDOWS)),
              f"- npz 대비 최대차: MFE종가 {d_mfc:.2e}, MFE고가 {d_mfh:.2e} / argmax {am}(npz {am_npz}) / 비용20 {cost[-1]*100:.2f}%(npz {cost_npz[-1]*100:.2f}%)",
              f"- 20일 최고점 이후 20일째 종가 {rc[-1]*100:+.2f}%, 순수익 {(rc[-1]-cost[-1])*100:+.2f}%",
              "", f"**판정: {'일치' if good else '불일치'}**", ""]

# 전 종목 신호 개수 재확인(무작위 1종목 전체 수동 스캔)
code = str(z["A21__code"][0])
df = pd.read_pickle(CACHE / "ohlcv" / f"{code}.pkl")
ac = [float(x) for x in df["aclose"].to_numpy()]
manual = 0
for i in range(62, len(ac) - 21):
    ma = sum(ac[i - 20:i + 1]) / 21
    ma_p = sum(ac[i - 21:i]) / 21
    if ac[i] > ma and ac[i - 1] <= ma_p:
        manual += 1
npz_cnt = int((z["A21__code"] == code).sum())
lines += [f"## 신호 개수 대조 — {code}", "",
          f"- 순수 파이썬 스캔(A21): {manual}건 / npz {npz_cnt}건 (유니버스·과열 필터 미적용 스캔이므로 npz ≤ 스캔이면 정합)", ""]

out = WORK / "mfe_verification.md"
out.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
print("ALL MATCH:", ok_all)
