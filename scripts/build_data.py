"""
Gridiron data pipeline.

Fetches free, public NFL data from nflverse and computes derived
fantasy metrics (grade, rank, boom/bust rate, game logs) for every
QB / RB / WR / TE / K / team defense.

Run manually with:  python scripts/build_data.py
Runs automatically every week via .github/workflows/refresh-data.yml

Outputs:
  data/search_index.json   (lightweight list, used for the sidebar/search)
  data/players.json        (full per-player profiles, used for the detail view)
"""

import csv
import io
import json
import urllib.request
from collections import defaultdict

BASELINE_SEASONS = ["2024"]
# ^ Add "2025" to this list as soon as nflverse publishes it (checked periodically —
#   as of this writing their player_stats release is still capped at 2024).
#   Everything downstream (rank, grade, boom/bust, projections) will automatically
#   pool games across every season listed here — no other code changes needed.

NFLVERSE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"

BOOM_BUST = {
    "QB": (25, 12), "RB": (20, 8), "WR": (20, 8),
    "TE": (15, 6), "K": (10, 5), "DEF": (10, 2),
}

MIN_GAMES = 4


def season_label():
    if len(BASELINE_SEASONS) == 1:
        return BASELINE_SEASONS[0]
    return "/".join(BASELINE_SEASONS)


def fetch_csv(url):
    req = urllib.request.Request(url, headers={"User-Agent": "gridiron-pipeline"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        text = resp.read().decode("utf-8")
    return csv.DictReader(io.StringIO(text))


def points_allowed_score(pa):
    if pa == 0: return 10
    if pa <= 6: return 7
    if pa <= 13: return 4
    if pa <= 20: return 1
    if pa <= 27: return 0
    if pa <= 34: return -1
    return -4


def build_offense():
    players = {}
    totals = defaultdict(lambda: {"sum": 0.0, "n": 0})
    r = fetch_csv(f"{NFLVERSE_BASE}/player_stats/player_stats.csv")
    for row in r:
        if row["season"] not in BASELINE_SEASONS or row["season_type"] != "REG":
            continue
        pos = row["position"]
        if pos not in ("QB", "RB", "WR", "TE"):
            continue
        name = row["player_display_name"]
        try:
            pts = float(row["fantasy_points_ppr"] or 0)
        except ValueError:
            pts = 0.0
        if name not in players:
            players[name] = {"name": name, "position": pos, "team": row["recent_team"], "games": []}
        players[name]["team"] = row["recent_team"]
        players[name]["games"].append({
            "season": row["season"], "week": row["week"], "opp": row["opponent_team"],
            "targets": row.get("targets") or "0", "receptions": row.get("receptions") or "0",
            "rec_yards": row.get("receiving_yards") or "0", "rush_yards": row.get("rushing_yards") or "0",
            "pass_yards": row.get("passing_yards") or "0", "pass_tds": row.get("passing_tds") or "0",
            "fantasy_ppr": str(round(pts, 1)),
        })
        totals[name]["sum"] += pts
        totals[name]["n"] += 1

    out = {}
    for name, p in players.items():
        n = totals[name]["n"]
        if n < MIN_GAMES:
            continue
        p["games"].sort(key=lambda g: (g["season"], int(g["week"])))
        p["avg_ppr"] = round(totals[name]["sum"] / n, 1)
        p["games_played"] = n
        out[name] = p
    return out


def build_kickers():
    kickers = {}
    totals = defaultdict(lambda: {"sum": 0.0, "n": 0})
    r = fetch_csv(f"{NFLVERSE_BASE}/player_stats/player_stats_kicking.csv")
    for row in r:
        if row["season"] not in BASELINE_SEASONS or row["season_type"] != "REG":
            continue
        name = row["player_display_name"]
        try:
            fgm = float(row.get("fg_made") or 0)
            xpm = float(row.get("pat_made") or 0)
        except ValueError:
            fgm = xpm = 0
        pts = fgm * 3 + xpm
        if name not in kickers:
            kickers[name] = {"name": name, "position": "K", "team": row["team"], "games": []}
        kickers[name]["team"] = row["team"]
        kickers[name]["games"].append({
            "season": row["season"], "week": row["week"], "opp": row.get("opponent_team") or "—",
            "fg_made": row.get("fg_made") or "0", "fg_att": row.get("fg_att") or "0",
            "fantasy_ppr": str(round(pts, 1)),
        })
        totals[name]["sum"] += pts
        totals[name]["n"] += 1

    out = {}
    for name, p in kickers.items():
        n = totals[name]["n"]
        if n < MIN_GAMES:
            continue
        p["games"].sort(key=lambda g: (g["season"], int(g["week"])))
        p["avg_ppr"] = round(totals[name]["sum"] / n, 1)
        p["games_played"] = n
        out[name] = p
    return out


def build_defenses():
    pts_allowed = defaultdict(dict)
    opp_of = defaultdict(dict)
    r = fetch_csv(f"{NFLVERSE_BASE}/schedules/games.csv")
    for row in r:
        if row["season"] not in BASELINE_SEASONS or row["game_type"] != "REG" or not row["home_score"]:
            continue
        key = (row["season"], row["week"])  # namespaced so multiple seasons don't collide on week number
        home, away = row["home_team"], row["away_team"]
        hs, aws = float(row["home_score"]), float(row["away_score"])
        pts_allowed[home][key] = aws
        pts_allowed[away][key] = hs
        opp_of[home][key] = away
        opp_of[away][key] = home

    def_agg = defaultdict(lambda: defaultdict(lambda: {"sacks": 0, "ints": 0, "fum_rec": 0, "tds": 0, "safety": 0}))
    r = fetch_csv(f"{NFLVERSE_BASE}/player_stats/player_stats_def.csv")
    for row in r:
        if row["season"] not in BASELINE_SEASONS or row["season_type"] != "REG":
            continue
        d = def_agg[row["team"]][(row["season"], row["week"])]
        d["sacks"] += float(row.get("def_sacks") or 0)
        d["ints"] += float(row.get("def_interceptions") or 0)
        d["fum_rec"] += float(row.get("def_fumble_recovery_opp") or 0)
        d["tds"] += float(row.get("def_tds") or 0)
        d["safety"] += float(row.get("def_safety") or 0)

    out = {}
    for team in pts_allowed:
        games, total, n = [], 0, 0
        for key in sorted(pts_allowed[team], key=lambda k: (k[0], int(k[1]))):
            season, wk = key
            pa = pts_allowed[team][key]
            s = def_agg[team].get(key, {"sacks": 0, "ints": 0, "fum_rec": 0, "tds": 0, "safety": 0})
            score = (points_allowed_score(pa) + s["sacks"] + s["ints"] * 2
                     + s["fum_rec"] * 2 + s["tds"] * 6 + s["safety"] * 2)
            games.append({
                "season": season, "week": wk, "opp": opp_of[team].get(key, "—"), "pts_allowed": pa,
                "sacks": s["sacks"], "ints": s["ints"], "fum_rec": s["fum_rec"],
                "def_tds": s["tds"], "fantasy_ppr": str(round(score, 1)),
            })
            total += score
            n += 1
        name = f"{team} Defense"
        out[name] = {"name": name, "position": "DEF", "team": team, "games": games,
                     "avg_ppr": round(total / n, 1) if n else 0, "games_played": n}
    return out


def enrich(all_players):
    by_pos = defaultdict(list)
    for name, p in all_players.items():
        by_pos[p["position"]].append((name, p["avg_ppr"]))

    rank_map = {}
    for pos, lst in by_pos.items():
        lst.sort(key=lambda x: x[1], reverse=True)
        n = len(lst)
        for i, (name, _) in enumerate(lst):
            rank_map[name] = {"rank": i + 1, "of": n, "percentile": round((1 - i / n) * 100)}

    for name, p in all_players.items():
        pos = p["position"]
        boom_thr, bust_thr = BOOM_BUST.get(pos, (20, 8))
        pts = [float(g["fantasy_ppr"]) for g in p["games"]]
        n = len(pts)
        p["boom_pct"] = round(sum(1 for x in pts if x >= boom_thr) / n * 100)
        p["bust_pct"] = round(sum(1 for x in pts if x < bust_thr) / n * 100)
        p["boom_thr"], p["bust_thr"] = boom_thr, bust_thr

        best_i, worst_i = pts.index(max(pts)), pts.index(min(pts))
        p["best_game"], p["worst_game"] = p["games"][best_i], p["games"][worst_i]

        rm = rank_map[name]
        p["rank"], p["rank_of"], p["start_score"] = rm["rank"], rm["of"], rm["percentile"]
        pct = rm["percentile"]
        p["grade"] = ("A+" if pct >= 90 else "A" if pct >= 80 else "B" if pct >= 65
                       else "C" if pct >= 45 else "D" if pct >= 25 else "F")

        levels = sorted(set([boom_thr + 5, boom_thr, max(boom_thr - 5, 1), bust_thr]), reverse=True)
        ladder = [{"label": f"{int(lv)}+", "pct": round(sum(1 for x in pts if x >= lv) / n * 100)} for lv in levels]
        ladder.append({"label": f"< {int(bust_thr)}", "pct": p["bust_pct"], "bust": True})
        p["ladder"] = ladder

        bg, wg = p["best_game"], p["worst_game"]
        if pos == "DEF":
            p["narrative"] = (f"{p['name']} averaged {p['avg_ppr']} fantasy points per game across "
                               f"{p['games_played']} games ({season_label()}), ranking #{p['rank']} of {p['rank_of']} defenses. "
                               f"Best outing: Week {bg['week']} vs {bg['opp']} ({bg['fantasy_ppr']} pts). "
                               f"Floor: Week {wg['week']} vs {wg['opp']} at {wg['fantasy_ppr']} pts.")
        elif pos == "K":
            p["narrative"] = (f"{p['name']} averaged {p['avg_ppr']} fantasy points per game across "
                               f"{p['games_played']} games ({season_label()}), ranking #{p['rank']} of {p['rank_of']} kickers. "
                               f"Best: Week {bg['week']} vs {bg['opp']} ({bg['fg_made']}/{bg['fg_att']} FG). "
                               f"Floor: Week {wg['week']} vs {wg['opp']} at {wg['fantasy_ppr']} pts.")
        else:
            p["narrative"] = (f"{p['name']} averaged {p['avg_ppr']} PPR points per game across "
                               f"{p['games_played']} games ({season_label()}), ranking #{p['rank']} of {p['rank_of']} {pos}s. "
                               f"Ceiling: Week {bg['week']} vs {bg['opp']} ({bg['fantasy_ppr']} pts). "
                               f"Floor: Week {wg['week']} vs {wg['opp']} at {wg['fantasy_ppr']} pts. "
                               f"Boom rate ({p['boom_thr']}+): {p['boom_pct']}%. Bust rate (under {p['bust_thr']}): {p['bust_pct']}%.")
    return all_players


def build_matchup_context():
    """
    Returns per-team, per-position average fantasy points allowed (used to
    gauge how favorable a matchup is), plus per-team average points scored
    (used for defense/DST projections). Pooled across BASELINE_SEASONS.
    """
    allowed = defaultdict(lambda: defaultdict(list))  # team -> pos -> [pts, ...]
    r = fetch_csv(f"{NFLVERSE_BASE}/player_stats/player_stats.csv")
    for row in r:
        if row["season"] not in BASELINE_SEASONS or row["season_type"] != "REG":
            continue
        pos = row["position"]
        if pos not in ("QB", "RB", "WR", "TE"):
            continue
        try:
            pts = float(row["fantasy_points_ppr"] or 0)
        except ValueError:
            continue
        allowed[row["opponent_team"]][pos].append(pts)

    games_per_team = defaultdict(int)
    avg_allowed = defaultdict(dict)
    for team, posmap in allowed.items():
        for pos, vals in posmap.items():
            avg_allowed[team][pos] = sum(vals) / len(BASELINE_SEASONS) / 17  # approx per game across pooled seasons

    points_scored = defaultdict(list)
    r = fetch_csv(f"{NFLVERSE_BASE}/schedules/games.csv")
    for row in r:
        if row["season"] not in BASELINE_SEASONS or row["game_type"] != "REG" or not row["home_score"]:
            continue
        points_scored[row["home_team"]].append(float(row["home_score"]))
        points_scored[row["away_team"]].append(float(row["away_score"]))
    avg_scored = {team: sum(v) / len(v) for team, v in points_scored.items() if v}

    return {"avg_allowed": dict(avg_allowed), "avg_scored": avg_scored}


def main():
    print("Fetching offense stats...")
    all_players = build_offense()
    print(f"  {len(all_players)} offensive players")

    print("Fetching kicker stats...")
    all_players.update(build_kickers())

    print("Fetching schedules + defensive stats...")
    all_players.update(build_defenses())

    print(f"Total players/teams: {len(all_players)}")
    print("Computing ranks, grades, boom/bust, narratives...")
    all_players = enrich(all_players)

    search_index = sorted(
        [{"name": p["name"], "position": p["position"], "team": p["team"],
          "grade": p["grade"], "avg": p["avg_ppr"], "rank": p["rank"], "of": p["rank_of"]}
         for p in all_players.values()],
        key=lambda x: -x["avg"],
    )

    with open("data/players.json", "w") as f:
        json.dump(all_players, f)
    with open("data/search_index.json", "w") as f:
        json.dump(search_index, f)

    print("Wrote data/players.json and data/search_index.json")


if __name__ == "__main__":
    main()
