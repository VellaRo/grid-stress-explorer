"""
analyze.py — turn raw Energy-Charts data into a tidy DataFrame and detect
"grid stress" events. Pure functions, no I/O except via fetch.py.

Definitions (kept honest / sourced from how the data is actually defined):
  - renewable sources: Solar, Wind onshore, Wind offshore, Biomass,
    Hydro (run-of-river, reservoir, pumped storage generation), Geothermal
  - fossil/dispatchable backstop: Fossil gas, hard coal, lignite, oil,
    coal-derived gas, Waste, Others
  - residual load = Load - renewable_generation
      (how much non-renewable capacity must fill the gap)
  - renewable share of load = renewable_generation / Load

Stress events we detect:
  1. NEGATIVE_PRICE   : spot price < 0 EUR/MWh (oversupply)
  2. NEAR_ZERO_RESIDUAL: residual load close to 0 or negative (system tightness)
  3. FOSSIL_RAMP_UP    : large hour-over-hour increase in fossil output
"""
from __future__ import annotations

import pandas as pd

RENEWABLE_KEYS = [
    "Solar", "Wind onshore", "Wind offshore", "Biomass",
    "Hydro Run-of-River", "Hydro water reservoir", "Hydro pumped storage",
    "Geothermal",
]
FOSSIL_KEYS = [
    "Fossil gas", "Fossil hard coal", "Fossil brown coal / lignite",
    "Fossil oil", "Fossil coal-derived gas", "Waste", "Others",
]


def to_frame(raw: dict) -> pd.DataFrame:
    """Build a long-but-wide DataFrame indexed by timestamp."""
    power = raw["power"]
    ts = pd.to_datetime(pd.Series(power["unix_seconds"]), unit="s")
    df = pd.DataFrame({"timestamp": ts})

    series = {pt["name"]: pt.get("data", []) for pt in power.get("production_types", [])}
    for name, vec in series.items():
        df[name] = vec

    # price
    price = raw.get("price", {})
    if price.get("unix_seconds"):
        p_ts = pd.to_datetime(pd.Series(price["unix_seconds"]), unit="s")
        p_df = pd.DataFrame({"timestamp": p_ts, "price_eur_mwh": price.get("price", [])})
        df = df.merge(p_df, on="timestamp", how="left")

    df = df.set_index("timestamp").sort_index()
    df.index.name = "timestamp"
    return df


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Add computed columns: renewable/fossil totals, residual load, shares."""
    df = df.copy()
    avail_r = [c for c in RENEWABLE_KEYS if c in df.columns]
    avail_f = [c for c in FOSSIL_KEYS if c in df.columns]

    df["renewable_mw"] = df[avail_r].sum(axis=1)
    df["fossil_mw"] = df[avail_f].sum(axis=1)
    df["load_mw"] = df.get("Load", pd.Series(0, index=df.index))
    df["residual_load_mw"] = df["load_mw"] - df["renewable_mw"]

    # renewable share of load, guard against zero/negative load
    load_safe = df["load_mw"].replace(0, pd.NA)
    df["renewable_share_load"] = (df["renewable_mw"] / load_safe).clip(upper=1.5)
    return df


def detect_events(df: pd.DataFrame, residual_threshold_mw: float = 500.0,
                  ramp_threshold_mw: float = 4000.0) -> pd.DataFrame:
    """Return a table of detected stress events.

    Three event types are flagged per 15-min row:
      1. NEGATIVE_PRICE    — spot price < 0 EUR/MWh (renewable oversupply)
      2. NEAR_ZERO_RESIDUAL— |residual load| <= threshold (system tightness;
                              little fossil capacity left to balance)
      3. FOSSIL_RAMP_UP    — fossil output jumped >= threshold vs prev interval
    """
    events = []
    df = df.copy()
    # hour-over-hour (actually 15-min-over-15-min) change in fossil output
    df["fossil_prev"] = df["fossil_mw"].shift(1)
    df["fossil_delta"] = df["fossil_mw"] - df["fossil_prev"]

    for ts, row in df.iterrows():
        # (1) oversupply: someone is paying to take power off the grid
        if pd.notna(row.get("price_eur_mwh")) and row["price_eur_mwh"] < 0:
            events.append({
                "timestamp": ts, "type": "NEGATIVE_PRICE",
                "value": round(row["price_eur_mwh"], 1),
                "detail": f"spot price {row['price_eur_mwh']:.1f} EUR/MWh (oversupply)",
            })
        # (2) tightness: renewables almost cover demand on their own
        if pd.notna(row.get("residual_load_mw")) and abs(row["residual_load_mw"]) <= residual_threshold_mw:
            events.append({
                "timestamp": ts, "type": "NEAR_ZERO_RESIDUAL",
                "value": round(row["residual_load_mw"], 1),
                "detail": f"residual load only {row['residual_load_mw']:.0f} MW (system tightness)",
            })
        # (3) flexibility need: fast dispatchable ramp to backfill a renewables dip
        if pd.notna(row.get("fossil_delta")) and row["fossil_delta"] >= ramp_threshold_mw:
            events.append({
                "timestamp": ts, "type": "FOSSIL_RAMP_UP",
                "value": round(row["fossil_delta"], 1),
                "detail": f"fossils ramped +{row['fossil_delta']:.0f} MW vs prev interval",
            })

    evt = pd.DataFrame(events)
    if not evt.empty:
        evt = evt.sort_values("timestamp").reset_index(drop=True)
    return evt


def resample(df: pd.DataFrame, grain: str = "15min") -> pd.DataFrame:
    """Aggregate the 15-min frame to a coarser grain for trend/period views.

    grain: '15min' | 'hour' | 'day' | 'week' | 'month'
    Means power/price series, sums load-type totals sensibly (mean is fine for
    MW levels when comparing shapes). Renewable share is recomputed from means.
    """
    rule = {"15min": "15min", "hour": "h", "day": "D",
            "week": "W", "month": "ME"}.get(grain, "15min")
    if rule == "15min":
        return df
    agg = df.resample(rule)
    out = agg.mean(numeric_only=True)
    # recompute derived columns that don't survive a naive mean
    avail_r = [c for c in RENEWABLE_KEYS if c in out.columns]
    avail_f = [c for c in FOSSIL_KEYS if c in out.columns]
    if avail_r:
        out["renewable_mw"] = out[avail_r].sum(axis=1)
    if avail_f:
        out["fossil_mw"] = out[avail_f].sum(axis=1)
    if "load_mw" in out.columns:
        out["residual_load_mw"] = out["load_mw"] - out.get("renewable_mw", 0)
        load_safe = out["load_mw"].replace(0, pd.NA)
        out["renewable_share_load"] = (out["renewable_mw"] / load_safe).clip(upper=1.5)
    return out


def summarize(df: pd.DataFrame) -> dict:
    """Headline stats for the selected range."""
    valid = df.dropna(subset=["load_mw"])
    return {
        "range_start": str(df.index.min()),
        "range_end": str(df.index.max()),
        "hours": int(len(df)),
        "avg_load_mw": round(valid["load_mw"].mean(), 0) if len(valid) else None,
        "max_load_mw": round(valid["load_mw"].max(), 0) if len(valid) else None,
        "avg_renewable_share": round(valid["renewable_share_load"].mean() * 100, 1) if len(valid) else None,
        "min_renewable_share": round(valid["renewable_share_load"].min() * 100, 1) if len(valid) else None,
        "peak_renewable_mw": round(df["renewable_mw"].max(), 0),
        "peak_fossil_mw": round(df["fossil_mw"].max(), 0),
        "min_price": round(df["price_eur_mwh"].min(), 1) if "price_eur_mwh" in df else None,
        "max_price": round(df["price_eur_mwh"].max(), 1) if "price_eur_mwh" in df else None,
        "neg_price_hours": int((df["price_eur_mwh"] < 0).sum()) if "price_eur_mwh" in df else 0,
    }


if __name__ == "__main__":
    from fetch import fetch_range
    from datetime import date
    raw = fetch_range(date(2026, 7, 15), date(2026, 7, 18))
    df = enrich(to_frame(raw))
    print("SUMMARY:", summarize(df))
    ev = detect_events(df)
    print("EVENTS:", len(ev))
    print(ev.head(10).to_string())
