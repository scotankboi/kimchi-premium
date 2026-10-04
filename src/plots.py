"""Figures for the README. Static PNGs, light surface, one axis per chart."""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import MaxNLocator, PercentFormatter  # noqa: E402

INK = {"primary": "#0b0b0b", "secondary": "#52514e", "muted": "#898781",
       "grid": "#e1e0d9", "baseline": "#c3c2b7", "surface": "#fcfcfb"}
SERIES = {"blue": "#2a78d6", "red": "#e34948"}


def _style(ax, fig) -> None:
    fig.patch.set_facecolor(INK["surface"])
    ax.set_facecolor(INK["surface"])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(INK["baseline"])
    ax.grid(axis="y", color=INK["grid"], linewidth=0.75, linestyle="-")
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK["muted"], labelcolor=INK["secondary"], labelsize=9, length=0)


def _pct_axis(ax, decimals: int = 0) -> None:
    """Percent ticks on 1-2-5 steps only, so no tick is rounded into a wrong label (2.5% shown as 2%)."""
    ax.yaxis.set_major_locator(MaxNLocator(nbins=7, steps=[1, 2, 5, 10]))
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=decimals))


def _minus(text: str) -> str:
    """Typographic minus for numbers formatted by hand (tick formatters already do this)."""
    return text.replace("-", "\u2212")


def _titles(fig, title: str, subtitle: str, source: str) -> None:
    fig.text(0.06, 0.95, title, fontsize=13, fontweight="bold", color=INK["primary"], ha="left", va="top")
    fig.text(0.06, 0.895, subtitle, fontsize=9.5, color=INK["secondary"], ha="left", va="top")
    fig.text(0.06, 0.02, source, fontsize=8, color=INK["muted"], ha="left", va="bottom")


def premium_timeseries(daily: pd.DataFrame, cfg: dict, path: Path, events: pd.DataFrame | None = None) -> None:
    """Chart 2: the daily premium since the first common observation, sign shaded, checked events numbered."""
    s = daily.set_index("date")["kp"].dropna()
    coin = cfg["markets"]["upbit_coin"].split("-")[1]

    fig, ax = plt.subplots(figsize=(10, 4.8), dpi=200)
    fig.subplots_adjust(left=0.06, right=0.9, top=0.74, bottom=0.12)
    _style(ax, fig)
    ax.fill_between(s.index, s.values, 0, where=s.values >= 0, color=SERIES["blue"], alpha=0.12,
                    linewidth=0, interpolate=True)
    ax.fill_between(s.index, s.values, 0, where=s.values < 0, color=SERIES["red"], alpha=0.25,
                    linewidth=0, interpolate=True)
    ax.plot(s.index, s.values, color=SERIES["blue"], linewidth=1.3, solid_joinstyle="round")
    ax.axhline(0, color=INK["baseline"], linewidth=1)

    _pct_axis(ax)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_xlim(s.index.min(), s.index.max())
    ax.set_ylim(min(s.min(), 0) * 1.25, s.max() * 1.08)

    # selective direct labels: the extremes and the latest value, nothing else
    peak, trough, last = s.idxmax(), s.idxmin(), s.index[-1]
    for when in (peak, trough):
        ax.annotate(_minus(f"{s[when]:+.1%}") + f"  {when:%Y-%m-%d}", (when, s[when]), xytext=(10, 0),
                    textcoords="offset points", fontsize=8.5, color=INK["secondary"], va="center")
    ax.annotate(_minus(f"{s[last]:+.1%}") + f"\n{last:%Y-%m-%d}", (last, s[last]), xytext=(6, 0), textcoords="offset points",
                fontsize=8.5, color=INK["primary"], va="center", annotation_clip=False)

    if events is not None and events["annotate"].any():
        fig.text(0.06, 0.845, mark_events(ax, daily, events), fontsize=8, color=INK["secondary"], ha="left",
                 va="top")
    _titles(fig, f"Korea premium on {coin}: Upbit vs Binance at the official USD/KRW rate",
            f"Daily, sampled at the New York noon FX fixing, {s.index.min():%b %Y} to {s.index.max():%b %Y}. "
            "Red shading = reverse premium (cheaper in Korea).",
            f"Premium = Upbit {coin}/KRW / (Binance {coin}/USDT x USD/KRW) - 1. "
            "Sources: Upbit, Binance public APIs; FRED DEXKOUS. Assumes USDT = USD.")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=INK["surface"])
    plt.close(fig)


SLOTS = {"kp": "#2a78d6", "usdt_prem": "#eb6834", "crypto_prem": "#1baf7a"}
NAMES = {"kp": "Total premium (KP)", "usdt_prem": "USDT premium", "crypto_prem": "Crypto-specific premium"}
CIRCLED = "①②③④⑤⑥⑦⑧⑨"


def _legend(ax, keys, loc="upper left") -> None:
    handles = [plt.Line2D([], [], color=SLOTS[k], linewidth=2) for k in keys]
    ax.legend(handles, [NAMES[k] for k in keys], loc=loc, frameon=False, fontsize=8.5,
              labelcolor=INK["secondary"], ncol=len(keys))


def mark_events(ax, daily: pd.DataFrame, events: pd.DataFrame) -> str:
    """Numbered markers on chart 2 for events that passed the hourly check; returns the key text."""
    s = daily.set_index("date")["kp"]
    keys = []
    for i, ev in enumerate(events[events["annotate"]].itertuples()):
        day = ev.time_utc.tz_convert(None).normalize()
        y = s.get(day, float("nan"))
        up = ev.kp_side == "high"
        ax.annotate(CIRCLED[i], (day, y), xytext=(0, 12 if up else -12), textcoords="offset points",
                    ha="center", va="center", fontsize=10, color=INK["primary"],
                    arrowprops={"arrowstyle": "-", "color": INK["muted"], "linewidth": 0.6})
        keys.append(f"{CIRCLED[i]} {day:%Y-%m-%d} {ev.label}")
    return "\n".join("   ".join(keys[i:i + 3]) for i in range(0, len(keys), 3))


def decomposition(spliced: pd.DataFrame, path: Path) -> None:
    """Chart 1: KP and its two factors; Bithumb segment shaded."""
    d = spliced.set_index("date")
    fig, ax = plt.subplots(figsize=(10, 4.8), dpi=200)
    fig.subplots_adjust(left=0.06, right=0.97, top=0.78, bottom=0.12)
    _style(ax, fig)
    b = d[d["source"] == "bithumb"].index
    if len(b):
        ax.axvspan(b.min(), b.max(), color="#f0efec", zorder=0, linewidth=0)
        ax.text(b.min(), 1.0, " Bithumb (both legs)", transform=ax.get_xaxis_transform(), fontsize=8,
                color=INK["muted"], va="top")
        ax.text(b.max(), 1.0, "  Upbit (both legs)", transform=ax.get_xaxis_transform(), fontsize=8,
                color=INK["muted"], va="top")
    ax.axhline(0, color=INK["baseline"], linewidth=1)
    for k, lw in (("kp", 2.2), ("usdt_prem", 1.2), ("crypto_prem", 1.2)):
        ax.plot(d.index, d[k], color=SLOTS[k], linewidth=lw, solid_joinstyle="round", zorder=3 if k != "kp" else 2)
    _pct_axis(ax)
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.set_xlim(d.index.min(), d.index.max())
    _legend(ax, ["kp", "usdt_prem", "crypto_prem"], loc="upper right")
    _titles(fig, "Almost all of the premium is the price of USDT in Korea",
            f"Daily at the NY-noon FX fixing, {d.index.min():%b %Y} to {d.index.max():%b %Y}. "
            "(1+KP) = (1+USDT premium) x (1+crypto-specific premium).",
            "Both KRW legs from the same exchange in each segment. Sources: Bithumb, Upbit, Binance, FRED DEXKOUS.")
    fig.savefig(path, facecolor=INK["surface"])
    plt.close(fig)


def overlap_check(upbit: pd.DataFrame, bithumb: pd.DataFrame, path: Path) -> None:
    """Chart 1b: the same two factors from Upbit and from Bithumb where both exist."""
    j = upbit.merge(bithumb, on="date", suffixes=("_u", "_b")).dropna(subset=["usdt_prem_u", "usdt_prem_b"])
    j = j.set_index("date")
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), dpi=200)
    fig.subplots_adjust(left=0.06, right=0.97, top=0.74, bottom=0.14, wspace=0.18)
    colors = {"u": "#2a78d6", "b": "#eb6834"}
    for ax, k in zip(axes, ("usdt_prem", "crypto_prem")):
        _style(ax, fig)
        ax.axhline(0, color=INK["baseline"], linewidth=1)
        for side, lw in (("b", 1.0), ("u", 1.0)):
            ax.plot(j.index, j[f"{k}_{side}"], color=colors[side], linewidth=lw, alpha=0.9)
        ax.set_title(NAMES[k], fontsize=10, color=INK["primary"], loc="left")
        _pct_axis(ax, 1 if k == "crypto_prem" else 0)
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        ax.set_xlim(j.index.min(), j.index.max())
    handles = [plt.Line2D([], [], color=colors[s], linewidth=2) for s in ("u", "b")]
    axes[0].legend(handles, ["Upbit", "Bithumb"], loc="upper left", frameon=False, fontsize=8.5,
                   labelcolor=INK["secondary"], ncol=2)
    _titles(fig, "Upbit and Bithumb give the same split where both exist",
            f"Daily at the NY-noon FX fixing, {j.index.min():%b %Y} to {j.index.max():%b %Y}. "
            "Each exchange uses its own BTC/KRW and USDT/KRW.",
            "Crypto-specific premium: near zero on both; the day-to-day wiggle is consistent with last-trade noise "
            "(bid-ask bounce, trade timing). Sources: Upbit, Bithumb, Binance, FRED.")
    fig.savefig(path, facecolor=INK["surface"])
    plt.close(fig)


def event_zooms(hourly: pd.DataFrame, panels: list[dict], path: Path) -> None:
    """Chart 3: hourly close-ups. The crypto-specific premium needs no FX rate, so it is the reliable intraday measure."""
    h = hourly.set_index("ts_utc")
    fig, axes = plt.subplots(2, 2, figsize=(10, 7.2), dpi=200)
    fig.subplots_adjust(left=0.07, right=0.97, top=0.775, bottom=0.08, hspace=0.42, wspace=0.2)
    for ax, p in zip(axes.flat, panels):
        _style(ax, fig)
        w = h.loc[p["start"]:p["end"]]
        ax.axhline(0, color=INK["baseline"], linewidth=1)
        ax.axvline(pd.Timestamp(p["mark"], tz="UTC"), color=INK["baseline"], linewidth=0.8, linestyle="-")
        for k in ("kp", "usdt_prem", "crypto_prem"):
            if w[k].notna().any():
                ax.plot(w.index, w[k], color=SLOTS[k], linewidth=1.8 if k == "kp" else 1.2)
        ax.set_title(p["title"], fontsize=10, color=INK["primary"], loc="left")
        _pct_axis(ax)
        span = w.index.max() - w.index.min()
        loc = mdates.HourLocator(interval=6) if span <= pd.Timedelta(days=2) else mdates.DayLocator()
        ax.xaxis.set_major_locator(loc)
        # offset label shows year and month only, so a window crossing midnight is not labelled with its last day
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(
            loc, offset_formats=["", "%Y", "%Y-%b", "%Y-%b", "%Y-%b", "%Y-%b"]))
        ax.set_xlim(w.index.min(), w.index.max())
        if p.get("note"):  # below the axis, level with the date offset label, so it never covers data
            ax.text(0.0, -0.155, p["note"], transform=ax.transAxes, fontsize=7.5, color=INK["muted"], va="top",
                    ha="left")
    handles = [plt.Line2D([], [], color=SLOTS[k], linewidth=2) for k in SLOTS]
    fig.legend(handles, [NAMES[k] for k in SLOTS], loc="upper left", bbox_to_anchor=(0.055, 0.872), frameon=False,
               fontsize=8.5, labelcolor=INK["secondary"], ncol=3)
    fig.text(0.06, 0.965, "Event close-ups, hourly (UTC)", fontsize=13, fontweight="bold", color=INK["primary"],
             va="top")
    fig.text(0.06, 0.925, "Grey line = documented event time (martial law 13:27, cascade 20:50 UTC), otherwise "
             "00:00 UTC of the date. Hourly KP uses the latest FX fixing,\nwhich can be a day old or more; the "
             "crypto-specific premium uses no FX rate.", fontsize=9, color=INK["secondary"], va="top", linespacing=1.4)
    fig.savefig(path, facecolor=INK["surface"])
    plt.close(fig)


def return_quintiles(tables: list[pd.DataFrame], path: Path) -> None:
    """Chart 4: median premium by BTC 30-day return quintile, one bar group per sample."""
    fig, ax = plt.subplots(figsize=(10, 4.6), dpi=200)
    fig.subplots_adjust(left=0.06, right=0.97, top=0.71, bottom=0.18)
    _style(ax, fig)
    ax.grid(axis="y", color=INK["grid"], linewidth=0.75)
    colors = ["#2a78d6", "#eb6834"]
    width = 0.36
    for j, (t, color) in enumerate(zip(tables, colors)):
        x = [i + (j - 0.5) * (width + 0.04) for i in range(len(t))]
        ax.bar(x, t["median_kp"], width=width, color=color, label=f"since {t['sample_start'].iloc[0][:4]}")
        for xi, v in zip(x, t["median_kp"]):
            ax.text(xi, v, f"{v:.1%}", ha="center", va="bottom", fontsize=8, color=INK["secondary"])
    t0 = tables[0]
    labels = [_minus(f"{b}\n{lo:+.0%} to {hi:+.0%}") for b, lo, hi in zip(t0["bucket"], t0["from"], t0["to"])]
    ax.set_xticks(range(len(tables[0])), labels)
    _pct_axis(ax, 1)
    ax.legend(frameon=False, fontsize=8.5, labelcolor=INK["secondary"], loc="upper right")
    _titles(fig, "The median premium is highest after BTC falls",
            "Median daily premium by quintile of BTC's trailing 30-day return (Binance). Quintile ranges shown "
            "for the full sample.\nSince 2019 the median falls from Q1 to Q4 and stays flat in Q5; the full-sample "
            "rise in Q5 comes from the 2017–18 mania.",
            "Association only. Daily NY-noon samples; each quintile has ~570–650 days.")
    fig.savefig(path, facecolor=INK["surface"])
    plt.close(fig)


def backtest_by_year(yearly: pd.DataFrame, bt: dict, path: Path, path_label: str, fx_label: str,
                     full_years: list[int]) -> None:
    """Chart 5: what one person could earn per year under the remittance cap, against two references.
    Starts at the first full year; later partial years are starred."""
    y = yearly.set_index("year")
    y = y[y.index >= full_years[0]]
    full = y.loc[full_years]
    series = [("profit_without_cap_usd", "Same rule, no cap (\\$10k pool reused)", "#c3c2b7"),
              ("hindsight_best_usd", "Best possible under the cap (hindsight)", "#86b6ef"),
              ("profit_usd", "Rule under the cap", "#2a78d6")]
    fig, ax = plt.subplots(figsize=(10, 4.8), dpi=200)
    fig.subplots_adjust(left=0.07, right=0.97, top=0.72, bottom=0.14)
    _style(ax, fig)
    width, gap = 0.26, 0.03
    for j, (col, label, color) in enumerate(series):
        x = [i + (j - 1) * (width + gap) for i in range(len(y))]
        ax.bar(x, y[col] / 1e3, width=width, color=color, label=label)
        if col == "profit_usd":
            for xi, v in zip(x, y[col] / 1e3):
                ax.text(xi, v, f"{v:.1f}", ha="center", va="bottom", fontsize=7.5, color=INK["primary"])
    ax.set_xticks(range(len(y)), [str(yr) if yr in full_years else f"{yr}*" for yr in y.index])
    ax.xaxis.set_major_locator(plt.FixedLocator(range(len(y))))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=6, steps=[1, 2, 5, 10]))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"\\${v:,.0f}k"))
    ax.legend(frameon=False, fontsize=8.5, labelcolor=INK["secondary"], loc="upper right")
    # "\$" because matplotlib reads a pair of dollar signs as math mode
    _titles(fig, f"Under a \\${bt['annual_remit_cap_usd'] / 1e3:,.0f}k annual cap, one person earns about "
                 f"\\${full['profit_usd'].median() / 1e3:.1f}k in a median year",
            f"{path_label}, {fx_label.replace('<=', '≤')}.\n"
            f"\\${bt['trade_size_usd'] / 1e3:,.0f}k trades, {bt['fx_spread']:.2%} FX "
            f"spread, entry when expected net return > {bt['entry_buffer']:.1%}. Bar labels: rule under the cap (\\$k).",
            "* partial year. The cap counts the dollars remitted (trade proceeds); the 2026 rule is applied to every "
            "year as a counterfactual.\nFees as of 2026-10-01. Sources: Upbit, Binance, FRED.")
    fig.savefig(path, facecolor=INK["surface"])
    plt.close(fig)


def sensitivity_heatmap(sens: pd.DataFrame, bt: dict, path: Path, fx_label: str) -> None:
    """Chart 6: median annual profit under the cap by FX spread and entry threshold, per path."""
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("blue", ["#f0efec", "#b7d3f6", "#5598e7", "#1c5cab", "#0d366b"])
    panels = [("btc_hedged", "BTC path, hedged"), ("usdt", "USDT path")]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.6), dpi=200)
    fig.subplots_adjust(left=0.1, right=0.97, top=0.70, bottom=0.14, wspace=0.28)
    vmax = sens["median_annual_profit_usd"].max()
    for ax, (key, title) in zip(axes, panels):
        s = sens[sens["path"] == key]
        grid = s.pivot(index="fx_spread", columns="entry_buffer", values="median_annual_profit_usd")
        ax.imshow(grid.to_numpy(), cmap=cmap, vmin=0, vmax=vmax, aspect="auto")
        for i in range(grid.shape[0]):
            for j in range(grid.shape[1]):
                v = grid.iat[i, j]
                ax.text(j, i, f"\\${v / 1e3:.1f}k", ha="center", va="center", fontsize=8,
                        color="#ffffff" if v > 0.55 * vmax else INK["primary"])
        ax.set_xticks(range(grid.shape[1]), [f"{c:.2%}".replace(".00%", "%") for c in grid.columns], fontsize=8.5)
        ax.set_yticks(range(grid.shape[0]), [f"{i:.2%}" for i in grid.index], fontsize=8.5)
        ax.set_xlabel("Entry threshold (expected net return)", fontsize=8.5, color=INK["secondary"])
        ax.set_ylabel("Bank FX spread", fontsize=8.5, color=INK["secondary"])
        ax.set_title(f"{title}, {s['years'].iloc[0]}", fontsize=10, color=INK["primary"], loc="left")
        ax.tick_params(colors=INK["muted"], labelcolor=INK["secondary"], length=0)
        for side in ax.spines.values():
            side.set_visible(False)
    fig.patch.set_facecolor(INK["surface"])
    _titles(fig, f"Median annual profit under the cap stays below \\${np.ceil(vmax / 1e3):.0f}k in every setting",
            f"One person, \\${bt['annual_remit_cap_usd'] / 1e3:,.0f}k/year cap, \\${bt['trade_size_usd'] / 1e3:,.0f}k "
            f"trades, {fx_label.replace('<=', '≤')}, full calendar years only.\n"
            "A higher threshold helps because the cap, not "
            "opportunity, is what binds.",
            "The hedged path starts with the perpetual (Sep 2019); the USDT path has one full year (2025). "
            "Fees as of 2026-10-01 (config.yaml).")
    fig.savefig(path, facecolor=INK["surface"])
    plt.close(fig)
