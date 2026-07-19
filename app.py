"""
app.py — Grid Stress Explorer dashboard (Streamlit + Plotly).
Run:  streamlit run app.py

Real German electricity data via the Fraunhofer ISE Energy-Charts API
(https://api.energy-charts.info — free, public, no key). The dashboard
watches the grid "breathe": renewable vs fossil generation, residual load,
and spot price, plus — for long ranges — the *relative active time* of
renewables (which hours / months they reliably dominate).

Design notes
------------
* Each plot section has its OWN time-range dropdown (no global control, no
  sidebar widgets) so you can, e.g., look at a Week for the generation mix
  but a 2-Year window for the seasonal view.
* Thresholds (residual, ramp, "active") are fixed constants below — no UI
  controls, by design.
* Colors are fixed per generation category so Solar / Wind offshore / etc.
  stay recognizable across every chart.
"""

from __future__ import annotations

import pandas as pd
from datetime import date, timedelta

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from fetch import fetch_range
from analyze import (
    to_frame, enrich, detect_events, summarize, resample,
    RENEWABLE_KEYS, FOSSIL_KEYS,
)

# --------------------------------------------------------------------------
# Fixed analysis configuration (intentionally no sidebar controls).
# --------------------------------------------------------------------------
RESIDUAL_THR_MW = 500.0      # residual load below this = system "tightness"
RAMP_THR_MW = 4000.0         # fossil output jump above this in 15 min = ramp-up
ACTIVE_THR_PCT = 50.0        # a period counts as "renewables active" above this %

# Distinct, recognizable color per generation category. Keeping a stable
# palette means Solar (gold) vs Wind offshore (deep blue) vs Wind onshore
# (light blue) read apart in every chart.
CATEGORY_COLORS = {
    # renewables
    "Solar": "#FFC107",                 # gold
    "Wind onshore": "#4FC3F7",          # light blue
    "Wind offshore": "#0D47A1",         # deep blue
    "Biomass": "#8D6E63",               # brown
    "Hydro Run-of-River": "#00BCD4",    # cyan
    "Hydro water reservoir": "#0097A7", # teal
    "Hydro pumped storage": "#80DEEA",  # pale cyan
    "Geothermal": "#BA68C8",            # purple
    # fossils
    "Fossil gas": "#E53935",            # red
    "Fossil hard coal": "#5D4037",      # dark brown
    "Fossil brown coal / lignite": "#A1887F",  # tan
    "Fossil oil": "#212121",            # near-black
    "Fossil coal-derived gas": "#EF5350",      # light red
    "Waste": "#757575",                 # gray
    "Others": "#BDBDBD",                # light gray
}
LOAD_COLOR = "black"        # reference line: total demand
RESIDUAL_COLOR = "orange"   # reference line: demand minus renewables

# The five time spans offered by every per-plot dropdown.
RANGE_OPTS = ["Day", "Week", "Month", "Year", "2 Years"]


def _range_dates(opt: str) -> tuple[date, date]:
    """Map a range label to (start, end) dates, anchored to today.

    End is always yesterday (API data lags a day or two); start reaches
    back far enough to cover the labelled span.
    """
    today = date.today()
    return {
        "Day": (today - timedelta(days=2), today - timedelta(days=1)),
        "Week": (today - timedelta(days=9), today - timedelta(days=2)),
        "Month": (today - timedelta(days=31), today - timedelta(days=1)),
        "Year": (today - timedelta(days=367), today - timedelta(days=1)),
        "2 Years": (today - timedelta(days=731), today - timedelta(days=1)),
    }[opt]


@st.cache_data(ttl=86400, show_spinner="Fetching real grid data…")
def load_range(start_str: str, end_str: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch + enrich + detect events for one date range.

    Cached per (start, end) for 24h so switching plots / reruns are instant
    and we don't re-hit the API. Returns (enriched_df, events_df).
    """
    s = date.fromisoformat(start_str)
    e = date.fromisoformat(end_str)
    raw = fetch_range(s, e)
    df = enrich(to_frame(raw))
    ev = detect_events(df, residual_threshold_mw=RESIDUAL_THR_MW,
                       ramp_threshold_mw=RAMP_THR_MW)
    return df, ev


def get_range(opt: str) -> tuple[pd.DataFrame, pd.DataFrame, int, str]:
    """Resolve a range label into (df, events, span_days, grain).

    `grain` is the aggregation used for long spans so charts stay readable:
    15-min for <=2 days, daily for <=40 days, weekly beyond that.
    """
    start, end = _range_dates(opt)
    df, ev = load_range(start.isoformat(), end.isoformat())
    span = (end - start).days
    grain = "15min" if span <= 2 else ("day" if span <= 40 else "week")
    return df, ev, span, grain


def section_picker(label: str, default: str) -> str:
    """Render a per-plot time-range dropdown and return its current value."""
    return st.selectbox(label, options=RANGE_OPTS, index=RANGE_OPTS.index(default),
                        key=f"pick_{label}")


# ==========================================================================
# Page scaffolding
# ==========================================================================
st.set_page_config(page_title="Grid Stress Explorer", layout="wide")

st.title("Grid Stress Explorer")
st.caption(
    "Real German electricity data via Fraunhofer ISE Energy-Charts API "
    "(free, public). Each section has its own time window — pick the span that "
    "makes that view meaningful."
)


# ==========================================================================
# 1) GENERATION MIX & SPOT PRICE
# Stacked-area of every generation category (distinct color each) plus the
# spot price underneath. Long ranges auto-aggregate to stay readable.
# ==========================================================================
st.header("Generation mix & spot price")
opt1 = section_picker("Generation mix — range", "Week")
df1, ev1, span1, grain1 = get_range(opt1)
df1_g = resample(df1, grain1)
# Use raw 15-min data for short spans; the resampled view for long ones.
src = df1 if span1 <= 10 else df1_g

# Let the user subset which categories are shown (all on by default).
all_cats = [c for c in (RENEWABLE_KEYS + FOSSIL_KEYS) if c in src.columns]
chosen = st.multiselect(
    "Show only these categories",
    options=all_cats,
    default=all_cats,
    help="Subset the stacked generation. Uncheck to hide a category (e.g. focus on solar vs offshore).",
)
if not chosen:
    st.warning("Select at least one category to plot.")
    chosen = []

# Headline metrics for the chosen window.
s1 = summarize(df1)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Avg load", f"{s1['avg_load_mw']:.0f} MW" if s1['avg_load_mw'] else "–")
c2.metric("Avg renewable share", f"{s1['avg_renewable_share']}%" if s1['avg_renewable_share'] is not None else "–")
c3.metric("Peak fossil", f"{s1['peak_fossil_mw']:.0f} MW" if s1['peak_fossil_mw'] else "–")
c4.metric("Negative-price hrs", s1['neg_price_hours'])

if chosen:
    # Two stacked areas (renewables + fossils) as SOLID opaque bands — no
    # transparency, so adjacent categories never bleed into one another.
    # Load and residual load are overlaid reference lines (not stacked).
    fig1 = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3],
                         vertical_spacing=0.08,
                         subplot_titles=("Generation mix (MW)", "Spot price (EUR/MWh)"))
    renew_chosen = [c for c in chosen if c in RENEWABLE_KEYS]
    fossil_chosen = [c for c in chosen if c in FOSSIL_KEYS]
    for grp, sg in (("renew", renew_chosen), ("fossil", fossil_chosen)):
        for c in sg:
            col = CATEGORY_COLORS.get(c, "#999999")
            fig1.add_trace(go.Scatter(
                x=src.index, y=src[c], name=c, stackgroup=grp,
                mode="lines", line=dict(width=0.5, color=col),
                fillcolor=col,        # fully opaque — true stacked look
            ), row=1, col=1)
    # Reference lines: total demand and what renewables DON'T cover.
    fig1.add_trace(go.Scatter(x=src.index, y=src["load_mw"], name="Load",
                              mode="lines", line=dict(color=LOAD_COLOR, width=1.5)), row=1, col=1)
    fig1.add_trace(go.Scatter(x=src.index, y=src["residual_load_mw"], name="Residual load",
                              mode="lines", line=dict(color=RESIDUAL_COLOR, width=1.5, dash="dot")), row=1, col=1)
    if "price_eur_mwh" in src.columns:
        fig1.add_trace(go.Scatter(x=src.index, y=src["price_eur_mwh"], name="Price",
                                  mode="lines", line=dict(color="blue", width=1.5),
                                  fill="tozeroy", fillcolor="rgba(52,152,219,0.15)"), row=2, col=1)
        if (src["price_eur_mwh"] < 0).any():
            fig1.add_hline(y=0, line=dict(color="red", width=1, dash="dash"), row=2, col=1)
    fig1.update_layout(height=640, hovermode="x unified",
                       legend=dict(orientation="h", y=-0.05),
                       margin=dict(l=40, r=20, t=40, b=20))
    fig1.update_yaxes(title_text="MW", row=1, col=1)
    fig1.update_yaxes(title_text="EUR/MWh", row=2, col=1)
    st.plotly_chart(fig1, width="stretch")
    if span1 > 10:
        st.caption(f"Plotted at '{grain1}' resolution ({len(src):,} pts) for readability. "
                   f"Solid stacked areas: renewables (green/blue/earth) stack separately from "
                   f"fossils (red/brown/gray). Solar = gold, Wind offshore = deep blue, "
                   f"Wind onshore = light blue. Load/Residual load are overlaid lines.")


# ==========================================================================
# 2) STRESS EVENTS
# Near-zero residual, negative-price, and fossil ramp-up intervals. Long
# ranges show aggregate counts (the raw table would be enormous).
# ==========================================================================
st.header("Detected stress events")
opt2 = section_picker("Stress events — range", "Week")
df2, ev2, span2, _ = get_range(opt2)
if ev2.empty:
    st.info("No stress events in this range with the current thresholds.")
elif span2 <= 10:
    # Short range: show every event in a sortable table.
    show = ev2.copy(); show["timestamp"] = show["timestamp"].astype(str)
    st.dataframe(show[["timestamp", "type", "value", "detail"]], width="stretch",
                 height=min(400, 40 + 30 * len(show)))
    st.caption(f"{len(ev2)} events: "
               f"{(ev2['type']=='NEGATIVE_PRICE').sum()} negative-price, "
               f"{(ev2['type']=='NEAR_ZERO_RESIDUAL').sum()} near-zero-residual, "
               f"{(ev2['type']=='FOSSIL_RAMP_UP').sum()} fossil ramp-ups.")
else:
    # Long range: collapse to counts so we don't dump tens of thousands of rows.
    nc = int((ev2['type'] == 'NEGATIVE_PRICE').sum())
    nz = int((ev2['type'] == 'NEAR_ZERO_RESIDUAL').sum())
    nr = int((ev2['type'] == 'FOSSIL_RAMP_UP').sum())
    worst = ev2.loc[ev2['value'].idxmin()] if 'NEGATIVE_PRICE' in ev2['type'].values else None
    st.markdown(f"- **{nc:,}** negative-price intervals (oversupply)  \n"
                f"- **{nz:,}** near-zero-residual intervals (system tightness)  \n"
                f"- **{nr:,}** fossil ramp-ups")
    if worst is not None:
        st.caption(f"Deepest negative price: {worst['value']:.1f} EUR/MWh on {worst['timestamp']}.")
    st.info("Long range: counts only. Pick Day/Week above to list every event.")


# ==========================================================================
# 3) RENEWABLE-SHARE TREND
# The headline long-run signal: renewable share of load over time, with a
# rolling mean to smooth the noise, plus price underneath.
# ==========================================================================
st.header("Renewable-share trend")
opt3 = section_picker("Trend — range", "Year")
df3, _, span3, grain3 = get_range(opt3)
df3_g = resample(df3, grain3)
if "renewable_share_load" in df3_g.columns:
    window = max(1, len(df3_g) // 12)   # ~12 points across the span
    fig_t = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.55, 0.45],
                          vertical_spacing=0.1,
                          subplot_titles=("Renewable share of load — trend", "Spot price (EUR/MWh)"))
    fig_t.add_trace(go.Scatter(x=df3_g.index, y=df3_g["renewable_share_load"] * 100,
                               name="Renewable share %", mode="lines",
                               line=dict(color="green", width=1)), row=1, col=1)
    roll = df3_g["renewable_share_load"].rolling(window, min_periods=1).mean() * 100
    fig_t.add_trace(go.Scatter(x=df3_g.index, y=roll, name=f"{window}-pt mean",
                               mode="lines", line=dict(color="darkgreen", width=3)), row=1, col=1)
    if "price_eur_mwh" in df3_g.columns:
        fig_t.add_trace(go.Scatter(x=df3_g.index, y=df3_g["price_eur_mwh"],
                                    name="Price", mode="lines",
                                    line=dict(color="blue", width=1)), row=2, col=1)
    fig_t.update_layout(height=460, hovermode="x unified",
                        legend=dict(orientation="h", y=-0.05),
                        margin=dict(l=40, r=20, t=40, b=20))
    fig_t.update_yaxes(title_text="%", row=1, col=1)
    fig_t.update_yaxes(title_text="EUR/MWh", row=2, col=1)
    st.plotly_chart(fig_t, width="stretch")


# ==========================================================================
# 4) DAILY SHAPE — average generation by hour of day, broken out per
# renewable (distinct colors) so solar's midday spike vs wind's steadier
# profile separate cleanly.
# ==========================================================================
st.header("Daily shape — average by hour of day")
opt4 = section_picker("Daily shape — range", "Month")
df4, _, span4, _ = get_range(opt4)
if "renewable_share_load" in df4.columns:
    hr = df4.copy(); hr["hour"] = hr.index.hour
    renew_in_df = [c for c in RENEWABLE_KEYS if c in hr.columns]
    chosen_r = st.multiselect(
        "Renewable categories",
        options=renew_in_df,
        default=renew_in_df,
        key="daily_renew",
        help="Which renewables to break out by hour. Each gets its own color.",
    )
    # Average MW per hour per chosen renewable -> stacked bars.
    hourly_mw = hr.groupby("hour")[chosen_r].mean() if chosen_r else hr.groupby("hour").mean(numeric_only=True)
    hourly_price = hr.groupby("hour")["price_eur_mwh"].mean()
    fig_h = make_subplots(specs=[[{"secondary_y": True}]])
    if chosen_r:
        for c in chosen_r:
            col = CATEGORY_COLORS.get(c, "#999999")
            fig_h.add_trace(go.Bar(x=hourly_mw.index, y=hourly_mw[c], name=c,
                                   marker_color=col, opacity=0.9), secondary_y=False)
    else:
        st.warning("Select at least one renewable category.")
    if hourly_price.notna().any():
        fig_h.add_trace(go.Scatter(x=hourly_price.index, y=hourly_price, name="Avg price",
                                   mode="lines+markers", line=dict(color="blue")),
                        secondary_y=True)
    fig_h.update_layout(height=360, hovermode="x unified", barmode="stack",
                        legend=dict(orientation="h", y=-0.08),
                        margin=dict(l=40, r=40, t=30, b=20))
    fig_h.update_xaxes(title_text="Hour of day (0-23)")
    fig_h.update_yaxes(title_text="Avg generation (MW)", secondary_y=False)
    fig_h.update_yaxes(title_text="EUR/MWh", secondary_y=True)
    st.plotly_chart(fig_h, width="stretch")
    st.caption("Stacked by renewable type: solar's midday spike, wind's steadier contribution, "
               "and biomass/hydro's flat baseload all separate out by hour.")


# ==========================================================================
# 5) WEEKLY SHAPE — average by day of week (needs >= ~2 weeks of data).
# ==========================================================================
st.header("Weekly shape — average by day of week")
opt5 = section_picker("Weekly shape — range", "Month")
df5, _, span5, _ = get_range(opt5)
if span5 >= 13 and "renewable_share_load" in df5.columns:
    wk = df5.copy(); wk["dow"] = wk.index.dayofweek
    dow = wk.groupby("dow").agg(
        renewable_share=("renewable_share_load", "mean"),
        load=("load_mw", "mean"),
        price=("price_eur_mwh", "mean"),
    )
    dow.index = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    fig_w = make_subplots(specs=[[{"secondary_y": True}]])
    fig_w.add_trace(go.Bar(x=dow.index, y=dow["load"] / 1000,
                           name="Avg load (GW)", marker_color="gray", opacity=0.5),
                    secondary_y=False)
    if dow["price"].notna().any():
        fig_w.add_trace(go.Scatter(x=dow.index, y=dow["price"], name="Avg price",
                                   mode="lines+markers", line=dict(color="blue")),
                        secondary_y=True)
    fig_w.update_layout(height=320, hovermode="x unified",
                        legend=dict(orientation="h", y=-0.08),
                        margin=dict(l=40, r=40, t=30, b=20))
    fig_w.update_xaxes(title_text="Day of week")
    fig_w.update_yaxes(title_text="Load (GW)", secondary_y=False)
    fig_w.update_yaxes(title_text="EUR/MWh", secondary_y=True)
    st.plotly_chart(fig_w, width="stretch")
    st.caption("Weekend load is typically lower; cheap renewables can push weekend prices down.")
elif span5 < 13:
    st.info("Pick a range of at least ~2 weeks to see the weekday pattern.")


# ==========================================================================
# 6) RELATIVE ACTIVE TIME OF RENEWABLES (long-range view)
# Two complementary views of WHEN renewables reliably dominate:
#   (a) Hour x Month heatmap of average renewable share of load.
#   (b) Per-renewable "active" share by hour (stacked).
# ==========================================================================
st.header("Relative active time of renewables")
opt6 = section_picker("Active time — range", "2 Years")
df6, _, span6, _ = get_range(opt6)
if span6 < 40:
    st.info("Pick Month / Year / 2 Years to see the seasonal 'relative active time' view.")
elif "renewable_share_load" in df6.columns:
    st.caption(
        f"Across this {span6}-day range: at which hours, and in which months, do renewables "
        f"reliably dominate? 'Active' = renewable share of load > {ACTIVE_THR_PCT:.0f}%."
    )

    # --- (a) Hour x Month heatmap of average renewable share of load ---
    hm = df6.copy(); hm["hour"] = hm.index.hour; hm["month"] = hm.index.month
    pivot = hm.pivot_table(index="hour", columns="month",
                           values="renewable_share_load", aggfunc="mean") * 100
    month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    pivot.columns = [month_labels[m - 1] for m in pivot.columns]
    pivot = pivot.sort_index()

    st.subheader("Hour × Month — average renewable share of load (%)")
    r, c = divmod(int(pivot.values.argmax()), pivot.shape[1])
    fig_hm = go.Figure(go.Heatmap(
        z=pivot.values, x=pivot.columns, y=pivot.index,
        colorscale="YlGn", zmid=50, zmin=0, zmax=100,
        colorbar=dict(title="Renew.<br>share %"),
        hovertemplate="Month %{x}, hour %{y}: %{z:.0f}%<extra></extra>",
    ))
    # Annotate the single most-renewable cell.
    fig_hm.add_annotation(x=pivot.columns[c], y=pivot.index[r],
                          text=f"peak {pivot.values[r, c]:.0f}%", showarrow=True,
                          arrowhead=2, font=dict(color="black"))
    fig_hm.update_layout(height=520, margin=dict(l=40, r=20, t=20, b=20),
                         yaxis_title="Hour of day", xaxis_title="Month")
    fig_hm.update_yaxes(dtick=1, autorange="reversed")
    st.plotly_chart(fig_hm, width="stretch")
    st.caption("Brighter = renewables dominate more often. Summer noons go net-green "
               "(share >100% = export); winter evenings are the fossil/import backstop.")

    # --- (b) Per-renewable "active" share by hour (stacked) ---
    # A renewable is "active" at an hour when its own output there exceeds its
    # overall median (i.e. it's pulling above its typical weight). Stacked per
    # hour shows WHICH source drives the renewable-dominated periods.
    st.subheader(f"Share of days each renewable is 'active' — by hour")
    st.caption(
        f"A renewable is 'active' at an hour when its own output there exceeds its overall "
        f"median (i.e. it's pulling above its typical weight). Stacked per hour shows which "
        f"source drives the renewable-dominated periods — e.g. solar at noon, wind in the "
        f"evening. Total height is not the >{ACTIVE_THR_PCT:.0f}% share bar; it's the mix of "
        f"active sources."
    )
    act = df6.copy(); act["hour"] = act.index.hour
    renew_in = [c for c in RENEWABLE_KEYS if c in act.columns]
    active_by_cat = {}
    for c in renew_in:
        med = act[c].median()
        if pd.notna(med) and med > 0:
            # % of days where this source beats its own median at that hour
            active_by_cat[c] = act.groupby("hour")[c].apply(
                lambda s: (s > med).mean() * 100)
        else:
            active_by_cat[c] = act.groupby("hour")[c].mean() * 0  # flat if no variation
    fig_a = go.Figure()
    for c in renew_in:
        col = CATEGORY_COLORS.get(c, "#999999")
        fig_a.add_trace(go.Bar(x=list(range(24)), y=active_by_cat[c].values,
                               name=c, marker_color=col, opacity=0.9))
    fig_a.update_layout(height=340, margin=dict(l=40, r=20, t=20, b=20),
                        barmode="stack", legend=dict(orientation="h", y=-0.08),
                        yaxis_title="% of days that source is 'active'",
                        xaxis_title="Hour of day")
    fig_a.update_yaxes(range=[0, 100])
    st.plotly_chart(fig_a, width="stretch")


# ==========================================================================
# INSIGHTS — auto-generated plain-language takeaways from the data.
# ==========================================================================
st.header("What the data says")
# Use the widest selected range (the trend picker) for context.
base_df, _, _, _ = get_range(opt3)
insights = []
s_base = summarize(base_df)
if s_base['avg_renewable_share'] is not None:
    insights.append(f"Over the **{opt3}** window renewables covered **{s_base['avg_renewable_share']}%** of load on average.")
if s_base['neg_price_hours']:
    insights.append(f"Spot price went negative for **{s_base['neg_price_hours']:,}** intervals — oversupply from weather-driven renewables.")
if "renewable_share_load" in base_df.columns:
    yr = base_df.copy(); yr["month"] = yr.index.month
    by_m = yr.groupby("month")["renewable_share_load"].mean() * 100
    best, worst = int(by_m.idxmax()), int(by_m.idxmin())
    ml = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    insights.append(f"Highest renewable-share month: **{ml[best-1]} ({by_m[best]:.0f}%)**; "
                    f"lowest: **{ml[worst-1]} ({by_m[worst]:.0f}%)**.")
for ins in insights:
    st.markdown(f"- {ins}")

st.markdown("---")
st.caption(
    "Built as a curiosity project on public data. Residual load = Load − renewable "
    "generation. Data: Energy-Charts by Fraunhofer ISE — not a BNetzA product. "
    "See README.md for methodology."
)
