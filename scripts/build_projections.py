"""
Gridiron current-week projections.

Builds a baseline projection for the CURRENT NFL week (auto-detected from today's
date against the real schedule — not hardcoded to Week 1) by combining:
  - Baseline per-player stats (from build_data.py's BASELINE_SEASONS)
  - The real, current roster for PROJECTION_SEASON (catches trades/free agency/retirements)
  - The real schedule for the current week (real opponents, real bye weeks)
  - Opponent matchup strength (avg fantasy points allowed by position, from the baseline seasons)

Every projection is honestly flagged:
  - "rookie_or_no_data": true  -> no baseline season stats exist for this player
  - "team_changed": true       -> player's current team differs from their baseline-season team
                                   (context has changed — new offense, new weapons, etc.)
  - "bye_week": true           -> this player's team has no game this week

Run manually with:  python scripts/build_projections.py
Runs automatically alongside build_data.py in the weekly refresh workflow (so the
"current week" naturally advances on its own as the season progresses — no manual
bumping needed).

Output: data/projections.json
"""

import csv
import json
import sys
import os
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(__file__))
from build_data import (
    fetch_csv, NFLVERSE_BASE, BASELINE_SEASONS, season_label,
    build_matchup_context,
)

PROJECTION_SEASON = "2026"  # bump this each year


def determine_current_week(games_rows):
    """
    Looks at the real schedule and picks the first week whose games haven't all
    been played yet (i.e. "this week" or the next upcoming one). Falls back to
    week 1 if the season hasn't started, and to the final week if the season's over.
    """
    weeks = {}
    for row in games_rows:
        if row["season"] != PROJECTION_SEASON or row["game_type"] != "REG":
            continue
        wk = int(row["week"])
        gd = row.get("gameday")
        if gd:
            weeks.setdefault(wk, []).append(gd)

    if not weeks:
        return 1, None

    today = date.today()
    for wk in sorted(weeks):
        last_game = max(datetime.strptime(d, "%Y-%m-%d").date() for d in weeks[wk])
        if last_game >= today:
            return wk, today
    return max(weeks), today  # season's over — show the final week rather than nothing


def load_week_schedule(target_week):
    """team -> opponent for the target week. Teams absent from the result are on a bye."""
    matchups = {}
    r = fetch_csv(f"{NFLVERSE_BASE}/schedules/games.csv")
    rows = list(r)
    for row in rows:
        if row["season"] != PROJECTION_SEASON or row["game_type"] != "REG":
            continue
        if int(row["week"]) != target_week:
            continue
        home, away = row["home_team"], row["away_team"]
        matchups[home] = {"opponent": away, "home_away": "home", "gameday": row.get("gameday")}
        matchups[away] = {"opponent": home, "home_away": "away", "gameday": row.get("gameday")}

    all_teams = set()
    for row in rows:
        if row["season"] == PROJECTION_SEASON and row["game_type"] == "REG":
            all_teams.add(row["home_team"])
            all_teams.add(row["away_team"])
    bye_teams = all_teams - set(matchups.keys())
    return matchups, bye_teams


FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K"}


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


def main():
    print(f"Loading baseline stats ({season_label()})...")
    with open("data/players.json") as f:
        baseline = json.load(f)

    print(f"Loading {PROJECTION_SEASON} rosters...")
    rosters = load_current_rosters()
    print(f"  {len(rosters)} players on current rosters (all positions)")

    print(f"Determining current week for {PROJECTION_SEASON}...")
    all_games = list(fetch_csv(f"{NFLVERSE_BASE}/schedules/games.csv"))
    target_week, today = determine_current_week(all_games)
    print(f"  Current week: {target_week} (today: {today})")

    week_matchups, bye_teams = load_week_schedule(target_week)
    print(f"  {len(week_matchups)} teams with a game, {len(bye_teams)} teams on bye: {sorted(bye_teams)}")

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
        is_bye = current_team in bye_teams
        wk_info = week_matchups.get(current_team)
        opponent = wk_info["opponent"] if wk_info else None

        base = baseline.get(name)
        record = {
            "name": name, "position": pos, "current_team": current_team,
            "week": target_week, "opponent": opponent, "bye_week": is_bye,
            "baseline_season": season_label(),
        }

        if not base:
            record.update({"rookie_or_no_data": True, "projected_points": None,
                            "note": "No baseline stats available (rookie, or too few games in baseline season)."})
            projections.append(record)
            continue

        team_changed = base.get("team") != current_team
        baseline_avg = base["avg_ppr"]

        if is_bye:
            projected = None
            method = "bye week — no game this week"
        elif opponent and opponent in avg_allowed and pos in avg_allowed.get(opponent, {}):
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
        elif is_bye:
            record["note"] = f"{current_team} is on a bye this week."
        projections.append(record)

    # --- Team defenses (DST) — not individual roster entries, handled separately ---
    print("Computing DST projections...")
    for name, base in baseline.items():
        if base.get("position") != "DEF":
            continue
        team = base["team"]
        is_bye = team in bye_teams
        wk_info = week_matchups.get(team)
        opponent = wk_info["opponent"] if wk_info else None
        baseline_avg = base["avg_ppr"]

        if is_bye:
            projected = None
            method = "bye week — no game this week"
        elif opponent and opponent in avg_scored and avg_scored[opponent]:
            factor = league_avg_scored / avg_scored[opponent]
            projected = round(baseline_avg * factor, 1)
            method = "baseline avg x opponent offense strength factor"
        else:
            projected = baseline_avg
            method = "baseline avg only (no opponent data)"

        record = {
            "name": name, "position": "DEF", "current_team": team,
            "week": target_week, "opponent": opponent, "bye_week": is_bye,
            "baseline_season": season_label(),
            "rookie_or_no_data": False, "team_changed": False,
            "baseline_team": team, "baseline_avg": baseline_avg,
            "projected_points": projected, "method": method,
            "grade": base.get("grade"), "rank": base.get("rank"), "rank_of": base.get("rank_of"),
        }
        if is_bye:
            record["note"] = f"{team} is on a bye this week."
        projections.append(record)

    projections.sort(key=lambda p: (p["projected_points"] is None, -(p["projected_points"] or 0)))

    with open("data/projections.json", "w") as f:
        json.dump({
            "projection_season": PROJECTION_SEASON,
            "week": target_week,
            "baseline_season": season_label(),
            "generated_note": "Baseline projection only — real matchup factor for QB/RB/WR/TE/DEF, "
                               "no injury/depth-chart data yet. See README for methodology.",
            "players": projections,
        }, f)

    print(f"Wrote data/projections.json — Week {target_week}, {len(projections)} players")

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
