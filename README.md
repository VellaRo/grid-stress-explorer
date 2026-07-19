# Grid Stress Explorer

A small curiosity project that visualizes how the German electricity grid
actually behaves — hour by hour — using **real, public data**.

You watch the grid "breathe": weather-driven renewables (solar, wind) set the
base, and dispatchable fossils + imports ramp up and down to fill the gap.
When that gap gets tight, or renewables oversupply the grid, you see it:
negative spot prices, fossil ramp-ups, near-zero residual load.

## What it does

- Pulls generation stack, load, residual load and spot price for any date range
  (15-minute resolution) from the Fraunhofer ISE **Energy-Charts API**.
- Shows a true **stacked-area** generation mix where every source has its own
  distinct color (Solar = gold, Wind offshore = deep blue, Wind onshore = light
  blue, fossils in reds/browns) so renewables vs fossils and source-vs-source
  read apart at a glance. Load and residual load are overlaid reference lines.
- Each plot section has its **own time-range dropdown** (Day / Week / Month /
  Year / 2 Years) — no global control, no sidebar widgets — so you pick the
  span that fits each view.
- Breaks the **daily shape** down per renewable (stacked bars by hour of day)
  and the **weekly shape** by day of week, revealing solar's midday spike vs
  wind's steadier profile.
- Plots the **renewable-share trend** over time with a rolling mean.
- For long ranges, shows the **relative active time** of renewables: an
  Hour x Month heatmap of average renewable share, plus a per-renewable
  "active by hour" stacked bar (a source is "active" when its output beats its
  own median at that hour).
- Auto-detects and lists / counts **stress events**:
  - `NEGATIVE_PRICE` — spot price < 0 EUR/MWh (oversupply)
  - `NEAR_ZERO_RESIDUAL` — residual load ~ 0 (system tightness)
  - `FOSSIL_RAMP_UP` — large 15-min fossil increase (flexibility need)

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py
```

Data is cached to `./cache` for 24h so reruns don't re-hit the API.

## Data source (honest note)

All data comes from [Energy-Charts](https://www.energy-charts.info) by
**Fraunhofer ISE** — a free, public, no-key API aggregating German/European
grid data. It is *not* a BNetzA internal system and *not* the official
"Marktkommunikation" (MaKo) message infrastructure.

Methodology:
- **Renewable** = Solar, Wind onshore/offshore, Biomass, Hydro (run-of-river,
  reservoir, pumped-storage generation), Geothermal.
- **Fossil/dispatchable** = Fossil gas, hard coal, lignite, oil, coal-derived
  gas, Waste, Others.
- **Residual load** = Load − renewable generation. This is the standard
  "how much must non-renewables cover" metric and is the single most
  informative number in the dataset.
- Nuclear = 0 (Germany shut the last reactors down in April 2023).

## Why I built this

I'm interested in the intersection of energy regulation and the data/IT systems
that make it work — the kind of work done at the Bundesnetzagentur, e.g.
Beschlusskammer 6 ("Regulierung Elektrizitätsnetze"). But "interested in
regulation" is abstract until you look at the actual physics of the grid.

This tool started as a personal question: *what does a calm evening vs a stormy
day actually look like on the wire?* The answer — fossils breathing in and out
with the sun, prices going negative at solar noon, imports covering the gaps —
is the concrete reality behind every Netzentgelte and Netzzugang decision.
Building it forced me to actually understand residual load, dispatchability and
market pricing, which is the same fluency the role asks for. It's a hobby
project, not a certified regulatory tool — and that's the point.
