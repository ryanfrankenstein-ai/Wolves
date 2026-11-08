from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, url_for

from feeds import read_team


app = Flask(__name__)
CENTRAL = ZoneInfo("America/Chicago")
ROSTER = json.loads(Path(__file__).with_name("roster.json").read_text(encoding="utf-8"))
TEAMS = ROSTER["teams"]
PLAYERS = ROSTER["players"]
TEAM_BY_ESPN = {str(team["espn"]): key for key, team in TEAMS.items() if team.get("espn")}
TEAM_BY_NAME = {team["name"].casefold(): key for key, team in TEAMS.items()}
TEAM_BY_HOST = {urlsplit(team["schedule"]).hostname.removeprefix("www."): key for key, team in TEAMS.items()}
ACTIVE_NAMES = {key: [player["name"] for player in PLAYERS if player["team"] == key and not player.get("archive")]
                for key in TEAMS}
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
                result = read_team(team, ACTIVE_NAMES[key])
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


def game_with_players(game):
    matchup = []
    for team in game.get("matchup", []):
        key = TEAM_BY_ESPN.get(team["id"]) if "id" in team else (
            TEAM_BY_HOST.get((team.get("host") or "").removeprefix("www.")) or TEAM_BY_NAME.get(team["name"].casefold()))
        matchup.append({**team, "players": ACTIVE_NAMES.get(key, [])})
    return {**game, "matchup": matchup}


def dashboard():
    global checking
    now = datetime.now(CENTRAL)
    with lock:
        if not checking and time.monotonic() >= next_check and not os.environ.get("WOLVES_OFFLINE"):
            checking = True
            threading.Thread(target=refresh, daemon=True).start()
        data, refreshing = dict(cache), checking
    today = now.date().isoformat()
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    games = {}
    for result in data.values():
        for game in result.get("games", []):
            previous, freshness = games.get(game["id"], ({}, (False, "")))
            current_freshness = (not result["delayed"], result.get("checked", ""))
            current = {**game, "delayed": result["delayed"]}
            if current_freshness < freshness:
                previous, current = current, previous
            lines = {**previous.get("lines", {}), **current.get("lines", {})}
            games[game["id"]] = ({**current, "lines": lines,
                                  "broadcasts": current.get("broadcasts") or previous.get("broadcasts", []),
                                  "watch_url": current.get("watch_url") or previous.get("watch_url", "")},
                                 max(freshness, current_freshness))
    ordered = sorted((game for game, _ in games.values()), key=lambda game: game["start"])
    players = []
    for player in PLAYERS:
        result = data.get(player["team"], {})
        line = result.get("players", {}).get(player["name"], {})
        team = TEAMS[player["team"]]
        players.append({**player, **line, "school": team["name"], "level": team["level"],
                        "program": team["program"], "schedule": team.get("schedule"), "stats_url": team.get("stats"),
                        "portrait": url_for("static", filename=player["photo"]) if player.get("photo") else None,
                        "team_logo": url_for("static", filename=team["logo"]) if team.get("logo") else None,
                        "delayed": result.get("delayed", False)})
    players.sort(key=lambda player: player.get("last", {}).get("start", ""), reverse=True)
    return {"players": players, "today": [game_with_players(game) for game in ordered if game["date"] == today],
            "upcoming": [game for game in ordered if game["date"] >= today and game["state"] == "pre"][:10],
            "live": [game_with_players(game) for game in ordered if game["state"] == "in" and game["date"] >= yesterday],
            "refreshing": refreshing,
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
