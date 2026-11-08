"use strict";

const get = (id) => document.getElementById(id);
const filters = ["search", "program", "school", "sort"].map(get);
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[ch]);
const statFields = {pts: "PTS", reb: "REB", ast: "AST", stl: "STL", blk: "BLK", min: "MIN"};
const shootingFields = {fg: "FG", "3pt": "3PT", ft: "FT"};
let data = JSON.parse(get("initial-data").textContent);
let timer;

function date(value) {
  return value ? new Intl.DateTimeFormat("en-US", {month: "short", day: "numeric", timeZone: "UTC"})
    .format(new Date(value + "T12:00:00Z")) : "";
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

function gameCard(game, showDate, showPlayers) {
  const matchup = game.matchup?.length === 2 ? game.matchup : null;
  const label = game.state === "pre" ? game.time : [game.status, !matchup && game.score].filter(Boolean).join(" · ");
  const gameLink = game.state === "post" ? "Box score" : game.state === "in" ? "Live game" : "Game details";
  const lines = Object.entries(game.lines || {});
  return `<article class="game ${game.state === "in" ? "is-live" : ""}">
    <div class="game-meta"><span>${escape(showDate ? date(game.date) + " · " + label : label)}</span>
    ${game.delayed ? "<span>Delayed</span>" : ""}</div>
    ${matchup ? `<div class="matchup">${matchup.map((team) => `<div class="matchup-team">
      ${image(team.logo, "team-mark", 24, 24)}<span>${escape(team.name)}</span>
      ${team.score != null ? `<b>${escape(team.score)}</b>` : ""}</div>`).join("")}</div>
      <div class="game-link">${link(game.url, gameLink, game.name)}</div>` : link(game.url, game.name)}
    ${showPlayers ? lines.map(([name, stats]) => {
      const player = data.players.find((entry) => entry.name === name);
      return `<p class="block-title live-player">${image(player?.portrait, "live-portrait", 30, 30)}${escape(name)}</p>${statsBlock(stats)}`;
    }).join("") : ""}
  </article>`;
}

function playerCard(player) {
  const avg = player.average;
  const last = player.last;
  return `<article class="player-card">
    <header class="player-head">
      ${image(player.portrait, "portrait", 72, 86)}
      <div class="player-identity"><p class="eyebrow">${escape(player.level)} · ${escape(player.program)}</p>
      <h3>${escape(player.name)}</h3><p class="player-school">${image(player.team_logo, "team-mark", 22, 22)}
      <span>${escape(player.school)} · ${escape(player.position)}</span></p></div>
      <span class="number">#${escape(player.number)}</span>
    </header>
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
  const node = get(id);
  if (node.dataset.rendered !== html) {
    node.innerHTML = html;
    node.dataset.rendered = html;
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
  const waiting = data.refreshing && !data.checked;
  const complete = data.checked && !data.delayed.length;
  const empty = (message) => `<p class="empty">${message}</p>`;
  update("today-games", data.today.map((game) => gameCard(game, false, false)).join("")
    || empty(waiting ? "Checking the college schedules…" : complete ? "No tracked games today." : "No games confirmed yet."));
  update("upcoming-games", data.upcoming.map((game) => gameCard(game, true, false)).join("")
    || empty(waiting ? "Checking the college schedules…" : "No upcoming games confirmed."));
  update("live-games", data.live.map((game) => gameCard(game, false, true)).join("")
    || empty("No live player lines available. Follow the game links for additional coverage."));
  const messages = [];
  if (data.checked) messages.push("Last checked " + new Intl.DateTimeFormat("en-US", {
    hour: "numeric", minute: "2-digit", timeZone: "America/Chicago", timeZoneName: "short"
  }).format(new Date(data.checked)));
  if (data.delayed.length) messages.push("Updates delayed for " + data.delayed.join(", "));
  get("feed-status").textContent = messages.join(" · ");
  renderPlayers();
}

async function refresh() {
  clearTimeout(timer);
  if (document.hidden) return;
  try {
    const response = await fetch("/api/dashboard", {cache: "no-store"});
    if (!response.ok) throw new Error("Dashboard unavailable");
    data = await response.json();
    render();
  } catch {
    get("feed-status").textContent = "Dashboard update delayed. Showing the most recent available data.";
  } finally {
    if (!document.hidden) timer = setTimeout(refresh, data.refreshing ? 4000 : data.live.length ? 15000 : 30000);
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
timer = setTimeout(refresh, 2500);
