from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import lru_cache
import json
import re
from urllib.parse import quote, urljoin, urlsplit
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


CT = ZoneInfo("America/Chicago")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball"
KEYS = {"PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk",
        "MIN": "min", "FG": "fg", "3PT": "3pt", "FT": "ft", "TOT": "reb", "A": "ast"}


def fetch(url):
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; WolvesBasketball/1.0)"}), timeout=9) as response:
        return response.read(7_000_000)


def normalized(name):
    name = re.sub(r"^#\s*\d+\s*", "", name.strip())
    if "," in name:
        last, first = name.split(",", 1)
        name = f"{first} {last}"
    return " ".join(re.findall(r"[a-z0-9]+", name.casefold()))


def game_date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(CT)


def score_line(teams):
    scores = [team.get("score") for team in teams]
    return " – ".join(map(str, scores)) if len(scores) == 2 and None not in scores else ""


def espn_matchup(participants, state):
    teams = []
    for participant in sorted(participants, key=lambda entry: entry["homeAway"] == "home"):
        school = participant["team"]
        score = participant.get("score")
        logos = school.get("logos") or []
        teams.append({"id": str(school["id"]), "name": school["displayName"],
                      "logo": school.get("logo") or (logos[0].get("href") if logos else None),
                      "score": (score.get("displayValue") if isinstance(score, dict) else score) if state != "pre" else None})
    return teams


def broadcast_names(competition):
    names = []
    for broadcast in competition.get("broadcasts", []) + competition.get("geoBroadcasts", []):
        if broadcast.get("region") not in (None, "", "us"):
            continue
        kind = broadcast.get("type", {}).get("shortName")
        if kind and kind.casefold() not in {"tv", "streaming"}:
            continue
        label = broadcast.get("media", {}).get("shortName")
        names.extend([label] if label else broadcast.get("names", []))
    return list(dict.fromkeys(name.strip() for name in names if name and name.strip()))


def espn_event(event):
    competition = event["competitions"][0]
    participants = competition["competitors"]
    status = (competition.get("status") or event["status"])["type"]
    start = game_date(competition.get("date") or event["date"])
    name = event.get("name") or " vs ".join(p["team"]["displayName"] for p in participants)
    matchup = espn_matchup(participants, status["state"])
    return {
        "id": f"espn-{event['id']}", "event": event["id"], "name": name,
        "date": start.date().isoformat(), "start": start.isoformat(),
        "time": start.strftime("%I:%M %p CT").lstrip("0") if competition.get("timeValid", True) else "Time TBD",
        "state": status["state"], "status": status.get("shortDetail") or status.get("description"),
        "url": f"https://www.espn.com/mens-college-basketball/game/_/gameId/{event['id']}",
        "score": score_line(matchup), "matchup": matchup,
        "broadcasts": broadcast_names(competition),
        "exhibition": any("exhibition" in note.get("headline", "").lower() for note in competition.get("notes", [])),
    }


def espn_lines(base, game, team_id):
    if game["state"] == "post":
        return final_lines(base, game["event"], team_id)
    summary = read_summary(base, game["event"])
    competitions = summary.get("header", {}).get("competitions", [])
    if competitions:
        competition = competitions[0]
        if not game["broadcasts"]:
            game["broadcasts"] = broadcast_names(competition)
        scores = {str(p["team"]["id"]): p.get("score") for p in competition.get("competitors", [])}
        for team in game["matchup"]:
            if scores.get(team["id"]) is not None:
                team["score"] = scores[team["id"]]
        game["score"] = score_line(game["matchup"]) or game["score"]
        status = competition.get("status", {}).get("type", {})
        if status.get("state") == "in":
            game["status"] = status.get("shortDetail") or game["status"]
    return box_lines(summary, team_id)


def read_summary(base, event):
    return json.loads(fetch(f"{base}/summary?event={event}"))


@lru_cache(maxsize=250)
def final_lines(base, event, team_id):
    lines = box_lines(read_summary(base, event), team_id)
    if not lines:
        raise ValueError("Final box score has no player lines yet")
    return lines


def box_lines(summary, team_id):
    box = summary.get("boxscore", {})
    lines = {}
    for team in box.get("players", []):
        if str(team.get("team", {}).get("id")) != team_id:
            continue
        for group in team.get("statistics", []):
            labels = group.get("labels", [])
            for item in group.get("athletes", []):
                if item.get("didNotPlay"):
                    continue
                athlete = item.get("athlete", {})
                stats = {KEYS[label]: value for label, value in zip(labels, item.get("stats", []))
                         if label in KEYS and value not in ("", "--", "-")}
                if stats:
                    if isinstance(item.get("starter"), bool):
                        stats["starter"] = item["starter"]
                    lines[normalized(athlete.get("displayName", ""))] = stats
    return lines


def averages(lines):
    output = {}
    for field in ("pts", "reb", "ast", "stl", "blk", "min"):
        values = [str(line[field]) for line in lines if field in line]
        if values and all(value.replace(".", "", 1).isdigit() for value in values):
            output[field] = round(sum(map(float, values)) / len(values), 1)
    return output


def read_espn(team, names):
    team_id = str(team["espn"])
    base = f"{ESPN}/mens-college-basketball"
    schedule = json.loads(fetch(f"{base}/teams/{team_id}/schedule"))
    games = [espn_event(event) for event in schedule["events"]]
    since = (datetime.now(CT).date() - timedelta(days=2)).isoformat()
    completed = [game for game in games if game["state"] == "post" and not game["exhibition"]]
    in_progress = [game for game in games if game["state"] == "in" and game["date"] >= since]
    return collect(games, names, sorted(completed, key=lambda game: game["start"]) + in_progress,
                   lambda game: espn_lines(base, game, team_id))


def collect(games, names, selected, read_box):
    mapped_names = {normalized(name): name for name in names}
    history = {name: [] for name in names}
    failed = False

    def get_lines(game):
        try:
            return read_box(game)
        except (ValueError, OSError, KeyError, TypeError):
            return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        for game, lines in zip(selected, executor.map(get_lines, selected)):
            if lines is None:
                failed = True
                continue
            tracked = {mapped_names[key]: stats for key, stats in lines.items() if key in mapped_names}
            if game["state"] == "in":
                game["lines"] = tracked
            else:
                for name, stats in tracked.items():
                    history[name].append({**game, "stats": stats})
    players = {}
    for name, entries in history.items():
        if entries:
            stats = [entry["stats"] for entry in entries]
            player = {"last": entries[-1], "average": averages(stats), "gp": len(entries), "partial": failed}
            if all("starter" in line for line in stats):
                starts = sum(line["starter"] for line in stats)
                player.update(starts=starts, starter=starts * 2 > len(entries))
            players[name] = player
    return {"games": games, "players": players, "partial": failed}


def official_event(row, team):
    opponent = row.select_one(".sidearm-schedule-game-opponent-name")
    date_node = row.select_one(".sidearm-schedule-game-opponent-date")
    date_text = date_node.get_text(" ", strip=True) if date_node else ""
    date_match = re.match(r"[A-Z][a-z]{2} \d{1,2}", date_text)
    if not opponent or not date_match:
        return None
    year = int(team["schedule"].rstrip("/").rsplit("/", 1)[-1][:4])
    day = datetime.strptime(f"{date_match[0]} {year}", "%b %d %Y").replace(tzinfo=CT)
    if day.month < 7:
        day = day.replace(year=year + 1)
    detail = row.select_one(".sidearm-schedule-game-result")
    result = detail.get_text(" ", strip=True) if detail else ""
    box = row.select_one(".sidearm-schedule-game-links-boxscore a[href]")
    live = row.select_one(".sidearm-schedule-game-links-stats a[href], a[aria-label^='Live stats' i]")
    video = row.select_one(".sidearm-schedule-game-links-video a[href]")
    video_url = urljoin(team["schedule"], video["href"]) if video else ""
    channels = [node.get_text(" ", strip=True) for node in row.select(".sidearm-schedule-game-coverage-tv-content, .sidearm-schedule-game-tv")]
    score = re.search(r"\d+\s*[-–]\s*\d+", result)
    done = "sidearm-schedule-game-completed" in row.get("class", [])
    scheduled = not (done or score)
    href = (box or live or {}).get("href")
    time_node = row.select_one(".sidearm-schedule-game-time")
    time_text = time_node.get_text(" ", strip=True) if time_node else date_text
    time_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?", time_text, re.I)
    if time_match:
        hour, minute, half = time_match.groups()
        tip = datetime.strptime(f"{hour}:{minute or '00'}{half.upper()}M", "%I:%M%p")
        day = day.replace(hour=tip.hour, minute=tip.minute)
    event_name = opponent.get_text(" ", strip=True)
    opponent_link = opponent.select_one("a[href]")
    opponent_host = urlsplit(opponent_link["href"]).hostname if opponent_link else None
    marker = "at" if "sidearm-schedule-away-game" in row["class"] else "vs."
    opponent_image = row.select_one(".sidearm-schedule-game-opponent-logo img")
    image_url = (opponent_image.get("data-src") or opponent_image.get("src")) if opponent_image else None
    opponent_logo = quote(urljoin(team["schedule"], image_url), safe=":/?&=%") if image_url else None
    points = re.findall(r"\d+", score[0]) if score else [None, None]
    matchup = [{"name": team["name"], "logo": f"/static/{team['logo']}" if team.get("logo") else None, "score": points[0]},
               {"name": event_name, "logo": opponent_logo, "score": points[1], "host": opponent_host}]
    return {"id": f"{team['name']}-{team['program']}-{row.get('data-game-id', day.isoformat())}",
            "name": f"{team['name']} {marker} {event_name}",
            "date": day.date().isoformat(), "start": day.isoformat(),
            "time": day.strftime("%I:%M %p").lstrip("0") + " CT" if time_match else "See schedule",
            "state": "pre" if scheduled else "post", "status": "Scheduled" if scheduled else "Final",
            "url": urljoin(team["schedule"], href) if href else team["schedule"],
            "score": score[0] if score else "", "matchup": matchup,
            "broadcasts": list(dict.fromkeys(channel.removeprefix("TV:").strip() for channel in channels if channel)),
            "watch_url": video_url if video and video["href"].strip() not in ("", "#") and urlsplit(video_url).scheme == "https" else "",
            "box": urljoin(team["schedule"], box["href"]) if box else "",
            "exhibition": "exhibition" in row.get_text(" ", strip=True).lower()}


@lru_cache(maxsize=250)
def official_lines(url):
    soup = BeautifulSoup(fetch(url), "html.parser")
    lines = {}
    for table in soup.select("table"):
        headers = [cell.get_text(" ", strip=True).upper() for cell in table.select("thead th")]
        starter_index = headers.index("GS") if "GS" in headers else None
        for row in table.select("tbody tr"):
            player = row.select_one(".boxscore_player_link")
            if not player:
                continue
            cells = {cell["data-label"].upper(): cell.get_text(" ", strip=True) for cell in row.select("td[data-label]")}
            stats = {KEYS[key]: value for key, value in cells.items() if key in KEYS and value not in ("", "–", "-")}
            columns = row.find_all(["td", "th"], recursive=False)
            if starter_index is not None and starter_index < len(columns):
                marker = columns[starter_index].get_text(" ", strip=True)
                if marker in ("*", ""):
                    stats["starter"] = marker == "*"
            if "pts" in stats and (stats.get("starter") or stats.get("min") not in ("0", "00:00", "0:00")):
                name = normalized(player.get_text(" ", strip=True))
                if starter_index is not None:
                    lines[name] = stats
                else:
                    # Sidearm can repeat players in half-by-half tables without a GS column.
                    lines.setdefault(name, stats)
    if not lines:
        raise ValueError("Official box score has no readable player lines")
    return lines


def read_official(team, names):
    try:
        source = fetch(team["schedule"])
    except (TimeoutError, URLError):
        source = fetch(team["schedule"])
    soup = BeautifulSoup(source, "html.parser")
    rows = soup.select(".sidearm-schedule-game")
    if not rows:
        raise ValueError("Official schedule layout was not recognized")
    games = [game for row in rows if (game := official_event(row, team))]
    if not games:
        raise ValueError("Official schedule dates were not recognized")
    selected = sorted((game for game in games if game["state"] == "post" and game["box"] and not game["exhibition"]),
                      key=lambda game: game["start"])
    result = collect(games, names, selected, lambda game: official_lines(game["box"]))
    if any(game["state"] == "post" and not game["box"] and game["score"] and not game["exhibition"] for game in games):
        result["partial"] = True
        for player in result["players"].values():
            player["partial"] = True
    return result


def read_team(team, names):
    if team.get("espn"):
        return read_espn(team, names)
    return read_official(team, names)
