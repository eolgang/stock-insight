#!/usr/bin/env python3
"""급등 2~3주 전 차트 그림 생성.

1) 대표 10사례 × 4시점(T-15 / T-10 / T-5 / T) 스냅샷 차트 → downloads/surge-pre-{n}.png
2) 유형별 평균 경로 차트 (T-40~T+40, 중앙값 + 사분위) → downloads/surge-type-T{n}.png
3) 유형 8개 한 장 요약 → downloads/surge-type-paths.png
노란 구간 = 각 시점 직전 2주(10거래일), 마지막 패널은 급등 직전 3주(15거래일).
"""
from __future__ import annotations

import os
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("BACKTEST_CACHE", REPO.parent / "work" / "backtest" / "cache"))
OHLCV = CACHE / "ohlcv"
WORK = REPO.parent / "work" / "backtest"
DL = REPO.parent / "downloads"
CASES = WORK / "surge_only_cases.pkl"

for f in fm.fontManager.ttflist:
    if "CJK KR" in f.name and "Sans" in f.name:
        plt.rcParams["font.family"] = f.name
        break
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 130

YEL = "#ffd93b"
YEL2 = "#ffe98a"

REPS = [
    ("005930", "2026-02"),
    ("117730", "2023-08"),
    ("271980", "2020-11"),
    ("267260", "2023-06"),
    ("000270", "2019-04"),
    ("259960", "2023-11"),
    ("010130", "2021-07"),
    ("377300", "2026-01"),
    ("086520", "2021-04"),
    ("095340", "2026-08"),
]


def load(code):
    d = pd.read_pickle(OHLCV / f"{code}.pkl")
    return d


def ma(s, n):
    return pd.Series(s).rolling(n).mean().to_numpy()


def draw_panel(ax, axv, d, end_idx, title, sub, weeks=10, tag=""):
    c = d["aclose"].to_numpy(float)
    o = d["aopen"].to_numpy(float)
    h = d["ahigh"].to_numpy(float)
    l = d["alow"].to_numpy(float)
    v = d["volume"].to_numpy(float)
    n = c.size
    i0 = max(0, end_idx - 40)
    xs = np.arange(i0, end_idx + 1)
    m20 = ma(c, 20); m60 = ma(c, 60); m120 = ma(c, 120)

    lo = np.nanmin(l[xs]); hi = np.nanmax(h[xs])
    pad = (hi - lo) * 0.08
    y0, y1 = lo - pad, hi + pad
    # 노란 구간: 이 시점 직전 weeks 거래일
    xs_start = max(i0, end_idx - weeks + 1)
    ax.axvspan(xs_start - 0.5, end_idx + 0.5, color=YEL2, alpha=0.55, zorder=0, lw=0)

    for i in xs:
        up = c[i] >= o[i]
        col = "#d32f2f" if up else "#1565c0"
        ax.vlines(i, l[i], h[i], color=col, lw=0.7, zorder=3)
        body_lo, body_hi = min(o[i], c[i]), max(o[i], c[i])
        if body_hi - body_lo < (y1 - y0) * 0.0015:
            body_hi = body_lo + (y1 - y0) * 0.0015
        ax.add_patch(Rectangle((i - 0.32, body_lo), 0.64, body_hi - body_lo,
                               facecolor=col, edgecolor=col, lw=0.4, zorder=4))
    ax.plot(xs, m20[xs], color="#f9a825", lw=1.1, label="MA20", zorder=5)
    ax.plot(xs, m60[xs], color="#6a1b9a", lw=1.1, label="MA60", zorder=5)
    ax.plot(xs, m120[xs], color="#2e7d32", lw=1.1, label="MA120", zorder=5)
    ax.set_xlim(i0 - 1, end_idx + 2)
    ax.set_ylim(y0, y1)
    ax.set_title(title, fontsize=8.5, pad=3, fontweight="bold")
    ax.set_xlabel(sub, fontsize=6.6, labelpad=2.5, color="#222")
    ax.tick_params(labelsize=6)
    ax.set_xticks([])
    ax.grid(alpha=0.15, lw=0.4)

    vr = v[xs] / np.nanmax(v[xs])
    cols = ["#d32f2f" if c[i] >= o[i] else "#1565c0" for i in xs]
    axv.bar(xs, v[xs], color=cols, width=0.7, alpha=0.85)
    axv.axvspan(xs_start - 0.5, end_idx + 0.5, color=YEL2, alpha=0.55, zorder=0, lw=0)
    axv.set_xlim(i0 - 1, end_idx + 2)
    axv.set_xticks([])
    axv.tick_params(labelsize=5.5)
    axv.set_yticks([])
    axv.grid(alpha=0.15, lw=0.4)


def case_figure(row, out_path, idx):
    d = load(row["code"])
    dates = d.index
    Tc = int(row["T"])
    ret = row["ret"]
    fig, axes = plt.subplots(2, 4, figsize=(13.6, 5.9), gridspec_kw=dict(height_ratios=[3.4, 0.9], hspace=0.30, wspace=0.09))
    labels = [(15, "T-15 (3주 전)"), (10, "T-10 (2주 전)"), (5, "T-5 (1주 전)"), (0, "T (급등 달 시작)")]
    ncols = 4
    for j, (back, lab) in enumerate(labels):
        i = Tc - back
        weeks = 15 if back == 0 else 10
        snap = row["snap"][j] or {}
        parts = []
        if snap.get("disp20") is not None:
            parts.append(f"MA20 {snap['disp20']*100:+.1f}%")
        if snap.get("disp60") is not None:
            parts.append(f"MA60 {snap['disp60']*100:+.1f}%")
        if snap.get("d52w") is not None:
            parts.append(f"52주고점 {snap['d52w']*100:+.0f}%")
        if snap.get("v10_20"):
            parts.append(f"거래량 {snap['v10_20']:.2f}배")
        if snap.get("brs"):
            parts.append("돌파:" + ",".join(snap["brs"][:3]))
        sub = " · ".join(parts[:2]) + "\n" + " · ".join(parts[2:])
        dt = str(dates[i].date())
        draw_panel(axes[0, j], axes[1, j], d, i, f"{lab}  {dt}", sub, weeks=weeks)
    axes[0, 0].legend(fontsize=5.6, loc="upper left", ncol=3, framealpha=0.85, handlelength=1.2)
    fig.suptitle(f"[{idx}] {row['name']} ({row['code']}) — {row['M']} 월봉 +{ret*100:.0f}% 급등 직전 3주  ·  노란 구간=직전 2주(마지막 패널은 3주)"
                 f"  ·  유형 {row['type']}", fontsize=9.5, y=0.995)
    fig.text(0.5, 0.005, f"급등 달 첫날 시가 매수 후 20일 {row['r20']*100:+.1f}% · 30일 {row['r30']*100:+.1f}% · 40일 {row['r40']*100:+.1f}% (비용 차감)",
             ha="center", fontsize=7.6, color="#333")
    fig.subplots_adjust(left=0.04, right=0.99, top=0.89, bottom=0.08)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)


def type_paths_figure(df, outdir):
    types = sorted(df["type"].unique())
    offs = np.arange(-40, 41)
    data = {}
    for t in types:
        P = np.vstack([np.array(p, float) for p in df[df["type"] == t]["path"]])
        with np.errstate(all="ignore"):
            med = np.nanmedian(P, axis=0)
            q1 = np.nanpercentile(P, 25, axis=0)
            q3 = np.nanpercentile(P, 75, axis=0)
        data[t] = (med, q1, q3, P.shape[0])

    def one(ax, t, small=False):
        med, q1, q3, k = data[t]
        ax.fill_between(offs, q1, q3, color="#90caf9", alpha=0.45, lw=0)
        ax.plot(offs, med, color="#0d47a1", lw=1.8)
        ax.axvspan(-15, 0, color=YEL2, alpha=0.75, lw=0, zorder=0)
        ax.axhline(1.0, color="#888", lw=0.6, ls=":")
        ax.axvline(0, color="#c62828", lw=0.8, ls="--", alpha=0.7)
        ax.set_title(f"{t}  (n={k})", fontsize=9 if not small else 7.5, fontweight="bold")
        ax.set_xlim(-40, 40)
        ax.grid(alpha=0.2, lw=0.4)
        ax.tick_params(labelsize=6.5)
        if not small:
            ax.set_xlabel("T 기준 거래일 (T=급등 달 직전 마지막 거래일)", fontsize=7.5)
            ax.set_ylabel("주가 (T=100)", fontsize=7.5)

    fig, axes = plt.subplots(2, 4, figsize=(14, 6.2))
    for ax, t in zip(axes.ravel(), types):
        one(ax, t)
    fig.suptitle("유형별 급등 전후 주가 경로 (중앙값 실선 · 파란 띠=사분위 25~75%)  ·  노란 구간=분석한 급등 직전 3주  ·  빨간 점선=T",
                 fontsize=10, y=0.98)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    p = outdir / "surge-type-paths.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    print("saved", p)

    for t in types:
        fig, ax = plt.subplots(figsize=(5.2, 3.2))
        one(ax, t)
        fig.tight_layout()
        name = t.split("_")[0]
        p = outdir / f"surge-type-{name}.png"
        fig.savefig(p, bbox_inches="tight")
        plt.close(fig)
        print("saved", p)


def main():
    df = pd.read_pickle(CASES)
    DL.mkdir(exist_ok=True)
    for k, (code, month) in enumerate(REPS, 1):
        sub = df[(df["code"] == code) & (df["M"] == month)]
        if sub.empty:
            print("missing", code, month)
            continue
        row = sub.iloc[0].to_dict()
        out = DL / f"surge-pre-{k}.png"
        case_figure(row, out, k)
        print("saved", out, row["name"], month, row["type"])
    type_paths_figure(df, DL)


if __name__ == "__main__":
    main()
