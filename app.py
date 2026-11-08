from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
import time
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template

from feeds import read_team


app = Flask(__name__)
CENTRAL = ZoneInfo("America/Chicago")
ROSTER = json.loads(Path(__file__).with_name("roster.json").read_text(encoding="utf-8"))
TEAMS = ROSTER["teams"]
PLAYERS = ROSTER["players"]
lock = threading.Lock()
cache = {}
checking = False
next_check = 0


def refresh():
    global checking, next_check
    try:
        teams = [(key, team) for key, team in TEAMS.items() if team.get("active", True)]

        def fetch(item):
            key, team = item
            try:
                result = read_team(team, [p["name"] for p in PLAYERS if p["team"] == key and not p.get("archive")])
                with lock:
                    previous = cache.get(key, {})
                    if result.get("partial") and previous.get("players"):
                        result["players"] = {**previous["players"], **result["players"]}
                    cache[key] = {**result, "delayed": False, "checked": datetime.now(timezone.utc).isoformat()}
            except Exception:
                logging.exception("Could not update %s", team["name"])
                with lock:
                    cache[key] = {**cache.get(key, {}), "delayed": True}

        with ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(fetch, teams))
    finally:
        with lock:
            live = any(game["state"] == "in" for result in cache.values() for game in result.get("games", []))
            delayed = any(result["delayed"] for result in cache.values())
            next_check = time.monotonic() + (25 if live or delayed else 70)
            checking = False


def dashboard():
    global checking
    now = datetime.now(CENTRAL)
    with lock:
        if not checking and time.monotonic() >= next_check and not os.environ.get("WOLVES_OFFLINE"):
            checking = True
            threading.Thread(target=refresh, daemon=True).start()
        data, refreshing = dict(cache), checking
    today = now.date().isoformat()
    games, live, upcoming = {}, {}, {}
    for key, result in data.items():
        for game in result.get("games", []):
            game = {**game, "delayed": result["delayed"]}
            if game["date"] == today:
                games[game["id"]] = game
            if game["state"] == "in" and game.get("lines"):
                live[game["id"]] = game
            if game["date"] >= today and game["state"] == "pre":
                upcoming[game["id"]] = game
    players = []
    for player in PLAYERS:
        result = data.get(player["team"], {})
        line = result.get("players", {}).get(player["name"], {})
        team = TEAMS[player["team"]]
        players.append({**player, **line, "school": team["name"], "level": team["level"],
                        "program": team["program"], "schedule": team.get("schedule"), "stats_url": team.get("stats"),
                        "delayed": result.get("delayed", False)})
    players.sort(key=lambda player: player.get("last", {}).get("start", ""), reverse=True)
    return {"players": players, "today": sorted(games.values(), key=lambda g: g["start"]),
            "upcoming": sorted(upcoming.values(), key=lambda g: g["start"])[:10],
            "live": list(live.values()), "refreshing": refreshing,
            "checked": max((r.get("checked", "") for r in data.values()), default=""),
            "delayed": [TEAMS[key]["name"] for key, result in data.items() if result["delayed"]],
            "date_label": now.strftime("%A, %B %d").replace(" 0", " "),
            "season": ROSTER["season"]}


@app.get("/")
def home():
    return render_template("index.html", data=dashboard(), teams=TEAMS)


@app.get("/api/dashboard")
def api_dashboard():
    return jsonify(dashboard()), 200, {"Cache-Control": "no-store"}


@app.get("/healthz")
def health():
    return {"status": "ok", "players": len(PLAYERS)}


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "5000")))
