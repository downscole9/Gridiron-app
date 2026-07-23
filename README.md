# Gridiron — Football Intelligence Terminal

A fantasy football intelligence platform, running on real NFL data (via nflverse, free/public).

## How it works
- `index.html` — the app itself, with two tabs:
  - **Player Profiles** — real season stats, grades, boom/bust rates, game logs
  - **Week 1 2026 Projections** — baseline projections combining last season's stats,
    each player's real current team, their real Week 1 2026 opponent, and opponent
    matchup strength
- `data/players.json` / `data/search_index.json` — real, computed season stats
- `data/projections_week1.json` — the Week 1 2026 baseline projections
- `scripts/build_data.py` — fetches nflverse data and regenerates the season-stats files
- `scripts/build_projections.py` — builds the Week 1 projections on top of that
- `.github/workflows/refresh-data.yml` — runs both scripts automatically every Tuesday
  at 9am UTC, and commits whatever changed. You can also trigger it manually from the
  **Actions** tab on GitHub → "Refresh player data" → **Run workflow**.

Every time the data refreshes and gets pushed, Vercel automatically redeploys the live site.

## Adding 2025 season stats (once nflverse publishes them)
Open `scripts/build_data.py` and change one line:
```python
BASELINE_SEASONS = ["2024"]        # before
BASELINE_SEASONS = ["2024", "2025"]  # after
```
Everything else — rank, grade, boom/bust, game logs, and the Week 1 projections —
automatically pools across every season in that list. No other code changes needed.

## Projection methodology (honestly stated)
- Baseline = each player's average PPR points per game in the baseline season(s)
- Adjusted by how many fantasy points their real Week 1 2026 opponent allowed to
  that position, relative to the league average
- Players who changed teams since the baseline season are flagged — new offense,
  new weapons, less predictable
- Players with no baseline-season stats (mostly rookies) are flagged as having no
  projection rather than guessed at
- This does **not** account for injuries, depth chart battles, or offseason scheme
  changes — it's a statistical baseline, not a full projection model

- Players with no baseline-season stats (mostly rookies, e.g. Jaxson Dart) are now
  searchable in **Player Profiles** too — shown with an honest "no baseline data yet"
  message instead of silently not appearing at all

## Status
- Real 2024 season stats for 502 players/teams
- Grade, rank, boom/bust %, best/worst game — all computed from real data
- Real Week 1 2026 baseline projections, using real rosters + real schedule
- Auto-refresh pipeline in place, now including projections

## Adding real AI-generated reports (optional, small cost)
1. Get an API key from **console.anthropic.com** (separate from a claude.ai subscription —
   this is pay-per-use, add a small amount of credit, e.g. $5)
2. In your GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**
3. Name it exactly `ANTHROPIC_API_KEY`, paste your key, **Add secret**
4. Next time the workflow runs (weekly, or triggered manually), every player will get a real
   Claude-written summary instead of the template. Rough cost: well under $1 for a full run
   of ~500 players using the cheapest model.
5. If the key is missing or a call fails for a specific player, that player just keeps their
   existing template summary — nothing breaks.

## Next steps
- Bump `BASELINE_SEASONS` once nflverse publishes 2025 (check periodically)
- AI-generated narrative reports (Claude API)
- Extend baseline projections beyond Week 1 as the season progresses
