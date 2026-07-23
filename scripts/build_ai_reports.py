"""
Gridiron AI reports.

Generates a short, real Claude-written scouting summary for every player,
using their actual computed season stats as the only source of truth handed
to the model (no invented plays, no stats not already in players.json).

Requires an ANTHROPIC_API_KEY environment variable (set as a GitHub Actions
secret — never hardcode it, never expose it client-side).

If the key is missing or a call fails for a given player, that player simply
keeps their existing template-based narrative (see build_data.py) — the app
never breaks or shows blank text, it just falls back gracefully.

Run manually with:  ANTHROPIC_API_KEY=sk-... python scripts/build_ai_reports.py
Runs automatically (with the key from a GitHub secret) in the weekly refresh workflow.
"""

import json
import os
import time
import urllib.request
import urllib.error

MODEL = "claude-haiku-4-5-20251001"  # cheapest current model — plenty for a short factual blurb
MAX_TOKENS = 130
DELAY_BETWEEN_CALLS = 0.3  # be polite to the API / avoid rate limits

SYSTEM_PROMPT = (
    "You are a concise fantasy football analyst. You will be given a player's real, "
    "already-computed season stats. Write exactly 2-3 sentences summarizing their season "
    "in a natural analyst voice. ONLY use the numbers provided — never invent stats, plays, "
    "injuries, or context not given to you. No preamble, no headers, just the summary text."
)


def build_user_prompt(p):
    pos = p["position"]
    lines = [
        f"Name: {p['name']}",
        f"Position: {pos}",
        f"Team: {p['team']}",
        f"Games played: {p['games_played']}",
        f"Average fantasy points per game (PPR): {p['avg_ppr']}",
        f"Position rank: #{p['rank']} of {p['rank_of']}",
        f"Grade: {p['grade']}",
        f"Boom rate ({p['boom_thr']}+ pts): {p['boom_pct']}%",
        f"Bust rate (under {p['bust_thr']} pts): {p['bust_pct']}%",
        f"Best game: Week {p['best_game']['week']} vs {p['best_game']['opp']}, "
        f"{p['best_game']['fantasy_ppr']} pts",
        f"Worst game: Week {p['worst_game']['week']} vs {p['worst_game']['opp']}, "
        f"{p['worst_game']['fantasy_ppr']} pts",
    ]
    return "\n".join(lines)


def call_claude(api_key, user_prompt):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["content"][0]["text"].strip()


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("No ANTHROPIC_API_KEY set — skipping AI report generation. "
              "Players will keep their template-based summaries.")
        return

    with open("data/players.json") as f:
        players = json.load(f)

    success, failed = 0, 0
    for i, (name, p) in enumerate(players.items()):
        try:
            prompt = build_user_prompt(p)
            text = call_claude(api_key, prompt)
            p["ai_narrative"] = text
            success += 1
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, Exception) as e:
            print(f"  Failed for {name}: {e}")
            failed += 1
        if (i + 1) % 50 == 0:
            print(f"  ...{i + 1}/{len(players)} processed")
        time.sleep(DELAY_BETWEEN_CALLS)

    with open("data/players.json", "w") as f:
        json.dump(players, f)

    print(f"AI reports: {success} succeeded, {failed} failed/skipped (kept template summary)")


if __name__ == "__main__":
    main()
