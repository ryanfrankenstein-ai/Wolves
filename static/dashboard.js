"use strict";

const get = (id) => document.getElementById(id);
const filters = ["search", "program", "school", "sort"].map(get);
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[ch]);
const statFields = {pts: "PTS", reb: "REB", ast: "AST", stl: "STL", blk: "BLK", min: "MIN"};
const shootingFields = {fg: "FG", "3pt": "3PT", ft: "FT"};
const dateFormat = new Intl.DateTimeFormat("en-US", {month: "short", day: "numeric", timeZone: "UTC"});
const timeFormat = new Intl.DateTimeFormat("en-US", {
  hour: "numeric", minute: "2-digit", timeZone: "America/Chicago", timeZoneName: "short"
});
const rendered = new Map();
let data = JSON.parse(get("initial-data").textContent);
let timer;
let fetching = false;
let etag = "";

function date(value) {
  return value ? dateFormat.format(new Date(value + "T12:00:00Z")) : "";
}

function link(url, label, detail = "") {
  if (!/^https:\/\//i.test(url || "")) return escape(label);
  const accessibleName = detail ? ` aria-label="${escape(label)} · ${escape(detail)}"` : "";
  return `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer"${accessibleName}>${escape(label)} ↗</a>`;
}

function image(url, className, width, height) {
  if (!/^(?:\/static\/media\/|https:\/\/)/i.test(url || "")) return "";
  return `<img class="${className}" src="${escape(url)}" alt="" width="${width}" height="${height}" loading="lazy" decoding="async">`;
}

function tiles(stats, fields, extraClass = "") {
  return `<div class="stat-grid ${extraClass}">${Object.entries(fields).map(([key, label]) =>
    `<div class="stat"><b>${escape(stats[key] ?? "—")}</b><span>${label}</span></div>`).join("")}</div>`;
}

function statsBlock(stats, shooting = true) {
  return tiles(stats, statFields) + (shooting && Object.keys(shootingFields).some((key) => key in stats)
    ? tiles(stats, shootingFields, "shooting") : "");
}

function gameTeam(team, showPlayers) {
  const names = showPlayers ? (team.players || []) : [];
  return `<div class="matchup-side"><div class="matchup-team">
    ${image(team.logo, "team-mark", 24, 24)}<span>${escape(team.name)}</span>
    ${team.score != null ? `<b>${escape(team.score)}</b>` : ""}</div>
    ${names.length ? `<ul class="game-players" aria-label="Tracked Wolves at ${escape(team.name)}">
      ${names.map((name) => `<li class="game-player">${escape(name)}</li>`).join("")}</ul>` : ""}</div>`;
}

function gameCard(game, {showDate = false, showPlayers = false} = {}) {
  const matchup = game.matchup?.length === 2 ? game.matchup : null;
  const label = game.state === "pre" ? game.time : [game.status, !matchup && game.score].filter(Boolean).join(" · ");
  const gameLink = game.state === "post" ? "Box score" : game.state === "in" ? "Live game" : "Game details";
  const channels = (game.broadcasts || []).filter(Boolean);
  const watch = /^https:\/\//i.test(game.watch_url || "") ? link(game.watch_url, "Watch stream", game.name) : "";
  return `<article class="game ${game.state === "in" ? "is-live" : ""}">
    <div class="game-meta"><span>${escape(showDate ? date(game.date) + " · " + label : label)}</span>
    ${game.delayed ? "<span>Delayed</span>" : ""}</div>
    ${matchup ? `<div class="matchup">${matchup.map((team) => gameTeam(team, showPlayers)).join("")}</div>
      <div class="game-link">${link(game.url, gameLink, game.name)}</div>` : link(game.url, game.name)}
    ${channels.length || watch ? `<div class="game-broadcast">
      ${channels.length ? `<span class="broadcast-label">TV / Stream</span> <span>${escape(channels.join(" · "))}</span>` : ""}
      ${watch}
    </div>` : ""}
  </article>`;
}

function playerHeader(player, label) {
  return `<header class="player-head">
      ${image(player.portrait, "portrait", 72, 86)}
      <div class="player-identity"><p class="eyebrow">${escape(label)}</p>
      <h3>${escape(player.name)}</h3><p class="player-school">${image(player.team_logo, "team-mark", 22, 22)}
      <span>${escape(player.school)} · ${escape(player.position)}</span></p></div>
      <span class="number">#${escape(player.number)}</span>
    </header>`;
}

function liveGame(game) {
  const players = data.players.filter((player) => game.lines?.[player.name]);
  return `<div class="live-game" role="group" aria-label="${escape(game.name)}">
    ${gameCard(game, {showPlayers: true})}
    ${players.map((player) => `<article class="player-card live-card">
      ${playerHeader(player, game.delayed ? "Live stats · delayed" : "Live stats")}
      <div class="player-body">${statsBlock(game.lines[player.name])}</div>
    </article>`).join("")}
  </div>`;
}

function playerCard(player) {
  const avg = player.average;
  const last = player.last;
  return `<article class="player-card">
    ${playerHeader(player, `${player.level} · ${player.program}`)}
    <div class="player-body">
      ${player.archive ? `<p class="empty">College roster archive · ${escape(player.archive)}</p>` : ""}
      ${player.previous?.length ? `<p class="empty">Previously at ${escape(player.previous.join(", "))}</p>` : ""}
      ${last ? `<div><p class="block-title">Last game · ${date(last.date)} · ${link(last.url, last.name)}</p>${statsBlock(last.stats)}</div>`
        : `<p class="empty">Player box score hasn’t been published in the available feed.</p>`}
      ${avg ? `<div><p class="block-title">Season averages · ${player.gp} GP${player.partial ? " · partial coverage" : ""}</p>${statsBlock(avg, false)}</div>` : ""}
      ${player.delayed ? '<p class="empty">Stats update delayed.</p>' : ""}
      <div class="links">${link(player.bio, "Official bio")}
        ${player.schedule && !player.archive ? link(player.schedule, "Schedule") : ""}
        ${player.stats_url && !player.archive ? link(player.stats_url, "Team stats") : ""}</div>
    </div>
  </article>`;
}

function update(id, html) {
  if (rendered.get(id) !== html) {
    get(id).innerHTML = html;
    rendered.set(id, html);
  }
}

function renderPlayers() {
  const [query, program, school, sort] = filters.map((control) => control.value);
  const text = query.toLocaleLowerCase().trim();
  const players = data.players.filter((p) =>
    (!school || p.team === school) && (!program || p.program === program)
    && [p.name, p.school, ...(p.previous || [])].some((value) => value.toLocaleLowerCase().includes(text)));
  if (sort === "name") players.sort((a, b) => a.name.localeCompare(b.name));
  if (statFields[sort]) players.sort((a, b) => (b.average?.[sort] ?? -1) - (a.average?.[sort] ?? -1));
  get("player-count").textContent = `${players.length} of ${data.players.length} players`;
  update("player-grid", players.map(playerCard).join("") || '<p class="empty">No players match these filters.</p>');
}

function render() {
  get("date-label").textContent = data.date_label;
  const ready = data.ready ?? Boolean(data.checked);
  const waiting = data.refreshing && !ready;
  const complete = ready && !data.delayed.length;
  const empty = (message) => `<p class="empty">${message}</p>`;
  update("today-games", data.today.map((game) => gameCard(game, {showPlayers: true})).join("")
    || empty(waiting ? "Checking the college schedules…" : complete ? "No tracked games today." : "No games confirmed yet."));
  update("upcoming-games", data.upcoming.map((game) => gameCard(game, {showDate: true})).join("")
    || empty(waiting ? "Checking the college schedules…" : "No upcoming games confirmed."));
  update("live-games", data.live.map(liveGame).join("")
    || empty("No tracked games are live right now."));
  renderFeedStatus();
  renderPlayers();
}

function renderFeedStatus() {
  const messages = [];
  if (data.checked) messages.push("Last checked " + timeFormat.format(new Date(data.checked)));
  if (data.delayed.length) messages.push("Updates delayed for " + data.delayed.join(", "));
  const status = get("feed-status");
  const message = messages.join(" · ");
  if (status.textContent !== message) status.textContent = message;
}

function refreshDelay() {
  if (data.refreshing) return 4000;
  const now = Date.now();
  const nearTip = data.today.some((game) => {
    const untilTip = Date.parse(game.start) - now;
    return game.state === "pre" && untilTip >= -14400000 && untilTip <= 900000;
  });
  return data.live.length || nearTip ? 15000 : 45000;
}

async function refresh() {
  clearTimeout(timer);
  if (document.hidden || fetching) return;
  fetching = true;
  try {
    const headers = etag ? {"If-None-Match": etag} : {};
    const response = await fetch("/api/dashboard", {cache: "no-store", headers});
    if (response.status === 304) {
      renderFeedStatus();
      return;
    }
    if (!response.ok) throw new Error("Dashboard unavailable");
    data = await response.json();
    etag = response.headers.get("ETag") || "";
    render();
  } catch {
    get("feed-status").textContent = "Dashboard update delayed. Showing the most recent available data.";
  } finally {
    fetching = false;
    if (!document.hidden) timer = setTimeout(refresh, refreshDelay());
  }
}

filters.forEach((control) => control.addEventListener("input", renderPlayers));
document.addEventListener("error", (event) => {
  if (event.target instanceof HTMLImageElement) event.target.hidden = true;
}, true);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearTimeout(timer);
  else refresh();
});
render();
timer = setTimeout(refresh, data.refreshing ? 2500 : refreshDelay());
