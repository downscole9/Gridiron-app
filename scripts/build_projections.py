"""
Gridiron Week 1 projections.

Builds a baseline projection for the upcoming season's Week 1 by combining:
  - Baseline per-player stats (from build_data.py's BASELINE_SEASONS)
  - The real, current roster for PROJECTION_SEASON (catches trades/free agency/retirements)
  - The real Week 1 schedule for PROJECTION_SEASON (real opponents)
  - Opponent matchup strength (avg fantasy points allowed by position, from the baseline seasons)

Every projection is honestly flagged:
  - "rookie_or_no_data": true  -> no baseline season stats exist for this player
  - "team_changed": true       -> player's current team differs from their baseline-season team
                                   (context has changed — new offense, new weapons, etc.)

Run manually with:  python scripts/build_projections.py
Runs automatically alongside build_data.py in the weekly refresh workflow.

Output: data/projections_week1.json
"""

import csv
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from build_data import (
    fetch_csv, NFLVERSE_BASE, BASELINE_SEASONS, season_label,
    build_matchup_context,
)

PROJECTION_SEASON = "2026"  # bump this each year


def load_current_rosters():
    """name -> current team, for the projection season."""
    roster_map = {}
    try:
        r = fetch_csv(f"{NFLVERSE_BASE}/rosters/roster_{PROJECTION_SEASON}.csv")
    except Exception as e:
        print(f"Could not load {PROJECTION_SEASON} rosters: {e}")
        return roster_map
    for row in r:
        name = row.get("full_name")
        team = row.get("team")
        pos = row.get("position")
        if name and team:
            roster_map[name] = {"team": team, "position": pos}
    return roster_map


def load_week1_schedule():
    """team -> Week 1 opponent, for the projection season."""
    matchups = {}
    r = fetch_csv(f"{NFLVERSE_BASE}/schedules/games.csv")
    for row in r:
        if row["season"] != PROJECTION_SEASON or row["week"] != "1" or row["game_type"] != "REG":
            continue
        home, away = row["home_team"], row["away_team"]
        matchups[home] = {"opponent": away, "home_away": "home", "gameday": row.get("gameday")}
        matchups[away] = {"opponent": home, "home_away": "away", "gameday": row.get("gameday")}
    return matchups


FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K"}


def main():
    print(f"Loading baseline stats ({season_label()})...")
    with open("data/players.json") as f:
        baseline = json.load(f)

    print(f"Loading {PROJECTION_SEASON} rosters...")
    rosters = load_current_rosters()
    print(f"  {len(rosters)} players on current rosters (all positions)")

    print(f"Loading {PROJECTION_SEASON} Week 1 schedule...")
    week1 = load_week1_schedule()
    print(f"  {len(week1)} teams scheduled for Week 1")

    print("Computing opponent matchup strength...")
    ctx = build_matchup_context()
    avg_allowed = ctx["avg_allowed"]
    avg_scored = ctx["avg_scored"]

    # league averages, used to normalize each opponent's strength into a multiplier
    league_avg_allowed = {}
    for pos in ("QB", "RB", "WR", "TE"):
        vals = [avg_allowed[t][pos] for t in avg_allowed if pos in avg_allowed[t]]
        league_avg_allowed[pos] = sum(vals) / len(vals) if vals else 1
    league_avg_scored = sum(avg_scored.values()) / len(avg_scored) if avg_scored else 1

    projections = []

    for name, roster_info in rosters.items():
        pos = roster_info["position"]
        if pos not in FANTASY_POSITIONS:
            continue  # skip linemen, long-snappers, defensive players not tracked for fantasy

        current_team = roster_info["team"]
        wk1 = week1.get(current_team)
        opponent = wk1["opponent"] if wk1 else None

        base = baseline.get(name)
        record = {
            "name": name, "position": pos, "current_team": current_team,
            "week1_opponent": opponent,
            "baseline_season": season_label(),
        }

        if not base:
            record.update({"rookie_or_no_data": True, "projected_points": None,
                            "note": "No baseline stats available (rookie, or too few games in baseline season)."})
            projections.append(record)
            continue

        team_changed = base.get("team") != current_team
        baseline_avg = base["avg_ppr"]

        if opponent and opponent in avg_allowed and pos in avg_allowed.get(opponent, {}):
            factor = avg_allowed[opponent][pos] / league_avg_allowed[pos]
            projected = round(baseline_avg * factor, 1)
            method = "baseline avg x opponent matchup factor"
        else:
            projected = baseline_avg
            method = "baseline avg only (no matchup adjustment available for this position yet)"

        record.update({
            "rookie_or_no_data": False,
            "team_changed": team_changed,
            "baseline_team": base.get("team"),
            "baseline_avg": baseline_avg,
            "projected_points": projected,
            "method": method,
            "grade": base.get("grade"),
            "rank": base.get("rank"), "rank_of": base.get("rank_of"),
        })
        if team_changed:
            record["note"] = f"Changed teams since {season_label()} baseline (was {base.get('team')}) — lower confidence."
        projections.append(record)

    # --- Team defenses (DST) — not individual roster entries, handled separately ---
    print("Computing DST projections...")
    for name, base in baseline.items():
        if base.get("position") != "DEF":
            continue
        team = base["team"]
        wk1 = week1.get(team)
        opponent = wk1["opponent"] if wk1 else None
        baseline_avg = base["avg_ppr"]

        if opponent and opponent in avg_scored and avg_scored[opponent]:
            factor = league_avg_scored / avg_scored[opponent]
            projected = round(baseline_avg * factor, 1)
            method = "baseline avg x opponent offense strength factor"
        else:
            projected = baseline_avg
            method = "baseline avg only (no opponent data)"

        projections.append({
            "name": name, "position": "DEF", "current_team": team,
            "week1_opponent": opponent, "baseline_season": season_label(),
            "rookie_or_no_data": False, "team_changed": False,
            "baseline_team": team, "baseline_avg": baseline_avg,
            "projected_points": projected, "method": method,
            "grade": base.get("grade"), "rank": base.get("rank"), "rank_of": base.get("rank_of"),
        })

    projections.sort(key=lambda p: (p["projected_points"] is None, -(p["projected_points"] or 0)))

    with open("data/projections_week1.json", "w") as f:
        json.dump({
            "projection_season": PROJECTION_SEASON,
            "baseline_season": season_label(),
            "generated_note": "Baseline projection only — real matchup factor for QB/RB/WR/TE/DEF, "
                               "no injury/depth-chart data yet. See README for methodology.",
            "players": projections,
        }, f)

    print(f"Wrote data/projections_week1.json ({len(projections)} players)")

    # --- Make rookies/no-baseline-data players searchable in Player Profiles too ---
    # (previously they only appeared in the Week 1 Projections tab, which was confusing —
    #  searching for a current rookie in Player Profiles just silently returned nothing)
    print("Adding no-baseline-data stub entries to players.json / search_index.json...")
    added = 0
    for name, roster_info in rosters.items():
        pos = roster_info["position"]
        if pos not in FANTASY_POSITIONS:
            continue
        if name in baseline:
            continue  # already has real stats, nothing to add
        baseline[name] = {
            "name": name, "position": pos, "team": roster_info["team"],
            "no_baseline_data": True, "games_played": 0, "games": [],
        }
        added += 1
    print(f"  {added} stub entries added")

    with open("data/players.json", "w") as f:
        json.dump(baseline, f)

    search_index = sorted(
        [{"name": p["name"], "position": p["position"], "team": p["team"],
          "grade": p.get("grade", "—"), "avg": p.get("avg_ppr", 0),
          "rank": p.get("rank"), "of": p.get("rank_of"),
          "no_baseline_data": p.get("no_baseline_data", False)}
         for p in baseline.values()],
        key=lambda x: (x["no_baseline_data"], -x["avg"]),
    )
    with open("data/search_index.json", "w") as f:
        json.dump(search_index, f)

    print(f"Wrote data/players.json and data/search_index.json ({len(baseline)} total entries)")


if __name__ == "__main__":
    main()
