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

from flask import Flask, abort, jsonify, render_template, request, url_for

from feeds import normalized, read_team


app = Flask(__name__)
CENTRAL = ZoneInfo("America/Chicago")
ROSTER = json.loads(Path(__file__).with_name("roster.json").read_text(encoding="utf-8"))
TEAMS = ROSTER["teams"]
PLAYERS = ROSTER["players"]
PLAYERS_BY_SLUG = {normalized(player["name"]).replace(" ", "-"): player for player in PLAYERS}
GAME_STATS = {"pts": "PTS", "reb": "REB", "ast": "AST", "min": "MIN", "stl": "STL",
              "blk": "BLK", "fg": "FG", "3pt": "3PT", "ft": "FT"}
TEAM_BY_ESPN = {str(team["espn"]): key for key, team in TEAMS.items() if team.get("espn")}
TEAM_BY_NAME = {team["name"].casefold(): key for key, team in TEAMS.items()}
TEAM_BY_HOST = {urlsplit(team["schedule"]).hostname.removeprefix("www."): key for key, team in TEAMS.items()}
ACTIVE_NAMES = {key: [player["name"] for player in PLAYERS if player["team"] == key and not player.get("archive")]
                for key in TEAMS}
TRACKED_TEAMS = [key for key, team in TEAMS.items() if team.get("active", True)]
lock = threading.Lock()
cache = {}
checking = False
next_checks = {}


def check_interval(team, result, now):
    recent = (now.date() - timedelta(days=1)).isoformat()
    games = result.get("games", [])
    if any(game["state"] == "in" and game["date"] >= recent for game in games):
        return 25
    for game in games:
        if game["state"] == "pre" and game["date"] >= recent:
            until_tip = (datetime.fromisoformat(game["start"]) - now).total_seconds()
            if -14400 <= until_tip <= 900:
                return 30 if team.get("espn") else 60
    return 60 if result.get("delayed") else 240


def refresh(keys):
    global checking

    def fetch(key):
        team = TEAMS[key]
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
        with lock:
            next_checks[key] = time.monotonic() + check_interval(team, cache[key], datetime.now(CENTRAL))

    try:
        with ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(fetch, keys))
    finally:
        with lock:
            checking = False


def game_with_players(game):
    matchup = []
    for team in game.get("matchup", []):
        key = TEAM_BY_ESPN.get(team["id"]) if "id" in team else (
            TEAM_BY_HOST.get((team.get("host") or "").removeprefix("www.")) or TEAM_BY_NAME.get(team["name"].casefold()))
        matchup.append({**team, "players": ACTIVE_NAMES.get(key, [])})
    return {**game, "matchup": matchup}


def refresh_snapshot():
    global checking
    with lock:
        if not checking and not os.environ.get("WOLVES_OFFLINE"):
            tick = time.monotonic()
            due = [key for key in TRACKED_TEAMS if tick >= next_checks.get(key, 0)]
            if due:
                checking = True
                threading.Thread(target=refresh, args=(due,), daemon=True).start()
        return dict(cache), checking


def dashboard():
    now = datetime.now(CENTRAL)
    data, refreshing = refresh_snapshot()
    today = now.date().isoformat()
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    games = {}
    for result in data.values():
        for game in result.get("games", []):
            if game["date"] < today and not (game["state"] == "in" and game["date"] >= yesterday):
                continue
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
    for slug, player in PLAYERS_BY_SLUG.items():
        result = data.get(player["team"], {})
        line = dict(result.get("players", {}).get(player["name"], {}))
        line.pop("game_log", None)
        team = TEAMS[player["team"]]
        players.append({**player, **line, "school": team["name"], "level": team["level"],
                        "program": team["program"], "schedule": team.get("schedule"), "stats_url": team.get("stats"),
                        "portrait": url_for("static", filename=player["photo"]) if player.get("photo") else None,
                        "team_logo": url_for("static", filename=team["logo"]) if team.get("logo") else None,
                        "game_log_url": url_for("player_game_log", slug=slug),
                        "delayed": result.get("delayed", False)})
    players.sort(key=lambda player: player.get("last", {}).get("start", ""), reverse=True)
    return {"players": players, "today": [game_with_players(game) for game in ordered if game["date"] == today],
            "upcoming": [game for game in ordered if game["date"] >= today and game["state"] == "pre"][:10],
            "live": [game_with_players(game) for game in ordered if game["state"] == "in" and game["date"] >= yesterday],
            "refreshing": refreshing,
            "ready": all(key in data for key in TRACKED_TEAMS),
            "checked": max((r.get("checked", "") for r in data.values()), default=""),
            "delayed": [TEAMS[key]["name"] for key, result in data.items() if result["delayed"]],
            "date_label": now.strftime("%A, %B %d").replace(" 0", " "),
            "season": ROSTER["season"]}


@app.get("/")
def home():
    return render_template("index.html", data=dashboard(), teams=TEAMS)


@app.get("/players/<slug>")
def player_game_log(slug):
    player = PLAYERS_BY_SLUG.get(slug)
    if player is None:
        abort(404)
    data, refreshing = refresh_snapshot()
    team = TEAMS[player["team"]]
    result = data.get(player["team"], {})
    line = result.get("players", {}).get(player["name"], {})
    games = [{**game, "date_label": datetime.fromisoformat(game["date"]).strftime("%b %d").replace(" 0", " ")}
             for game in line.get("game_log", [])]
    return render_template("game_log.html", player=player, team=team, season=ROSTER["season"],
                           stats=GAME_STATS, games=games, delayed=result.get("delayed"),
                           partial=result.get("partial"), pending=refreshing and not result and not player.get("archive"))


@app.get("/api/dashboard")
def api_dashboard():
    response = jsonify(dashboard())
    response.headers["Cache-Control"] = "private, no-cache"
    response.add_etag()
    return response.make_conditional(request)


@app.get("/healthz")
def health():
    return {"status": "ok", "players": len(PLAYERS)}


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "5000")))
