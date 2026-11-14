// Agent Studio front end: vanilla JS, live over Server-Sent Events.
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const S = { agents: [], projects: [], cli: null, settings: {}, cur: null, proj: null, drawer: null, totals: {} };
const COLS = ["Up Next", "In Progress", "Done"];
const COLC = { "Up Next": "#8f8da3", "In Progress": "#fbbf24", "Done": "#34d399" };

async function api(method, path, body) {
  const r = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty */ }
  if (!r.ok) throw new Error(data.detail || `${r.status} ${r.statusText}`);
  return data;
}
function toast(msg) { const t = $("toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove("show"), 3200); }
const agentById = id => S.agents.find(a => a.id === id);
const awake = a => a && a.live && ["working", "thinking"].includes(a.live.state);
const principalAgent = () => S.agents.find(a => a.kind === "principal");
const money = v => `$${(v || 0).toFixed(v >= 10 ? 1 : 2)}`;
function md(text) {
  const lines = esc(text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").split("\n");
  let html = "", inList = false;
  for (const l of lines) {
    if (/^\s*-\s+/.test(l)) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${l.replace(/^\s*-\s+/, "")}</li>`; }
    else { if (inList) { html += "</ul>"; inList = false; } if (l.trim()) html += `<p>${l}</p>`; }
  }
  return html + (inList ? "</ul>" : "");
}

// ---------------------------------------------------------------- load
async function load() {
  const st = await api("GET", "/api/state");
  Object.assign(S, { agents: st.agents, projects: st.projects, cli: st.cli, settings: st.settings, totals: st.totals });
  if (!S.cur || !S.projects.some(p => p.slug === S.cur)) {
    let saved = null; try { saved = localStorage.getItem("studio.project"); } catch (e) { /* private mode */ }
    S.cur = S.projects.some(p => p.slug === saved) ? saved : (S.projects[S.projects.length - 1] || {}).slug || null;
  }
  renderCli(); renderTabs(); renderFloor(); renderCrew();
  await loadProject();
}
async function loadProject() {
  if (!S.cur) { S.proj = null; renderEmpty(); renderHeader(); renderPrincipal(); renderBoard(); renderFeed(); renderMemory(); return; }
  try { S.proj = await api("GET", `/api/projects/${S.cur}`); } catch (e) { S.proj = null; }
  renderEmpty(); renderHeader(); renderPrincipal(); renderBoard(); renderFeed(); renderMemory(); renderTaskWho(); renderCounts();
}
function setProject(slug) {
  S.cur = slug; S.unread = 0; try { localStorage.setItem("studio.project", slug); } catch (e) { /* ignore */ }
  renderTabs(); renderFloor(); loadProject();
}

// ---------------------------------------------------------------- views (sidebar navigation)
const VIEWS = ["floor", "chat", "board", "activity", "memory"];
S.view = (() => { try { return localStorage.getItem("studio.view"); } catch (e) { return null; } })();
if (!VIEWS.includes(S.view)) S.view = "floor";
S.unread = 0;
function setView(v) {
  S.view = v; try { localStorage.setItem("studio.view", v); } catch (e) { /* ignore */ }
  const none = !S.projects.length;
  VIEWS.forEach(k => { $("view-" + k).hidden = none || k !== v; });
  document.querySelectorAll("#navList button").forEach(b => b.classList.toggle("on", b.dataset.view === v));
  if (v === "activity") S.unread = 0;
  if (v === "chat") { S.chatUnread = 0; renderChat(); setTimeout(() => { const t = $("chatText"); if (t) t.focus(); }, 50); }
  renderCounts();
}
document.querySelectorAll("#navList button").forEach(b => b.onclick = () => setView(b.dataset.view));
document.addEventListener("keydown", e => {   // 1-4 switch views (when not typing)
  if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || e.ctrlKey || e.altKey || e.metaKey) return;
  const i = ["1", "2", "3", "4", "5"].indexOf(e.key); if (i >= 0) setView(VIEWS[i]);
});
function renderCounts() {
  const proj = S.projects.find(p => p.slug === S.cur), tasks = (S.proj && S.proj.tasks) || [];
  const working = proj ? (proj.team || []).filter(id => awake(agentById(id))).length : 0;
  $("cntFloor").textContent = working ? `${working} working` : "";
  $("cntFloor").classList.toggle("hot", working > 0);
  const prog = tasks.filter(t => t.column === "In Progress").length, up = tasks.filter(t => t.column === "Up Next").length;
  $("cntBoard").textContent = tasks.length ? `${prog ? prog + " ▸ " : ""}${up} up next` : "";
  $("cntActivity").textContent = S.unread ? `${S.unread} new` : "";
  $("cntActivity").classList.toggle("hot", S.unread > 0);
  $("cntChat").textContent = S.chatUnread ? `${S.chatUnread} new` : "";
  $("cntChat").classList.toggle("hot", S.chatUnread > 0);
}

// ---------------------------------------------------------------- talk to Sol
S.chat = { slug: null, messages: [] }; S.chatUnread = 0;
const SUGGEST = ["What's the status right now?", "What should we do next?", "Who's working on what?", "Anything blocking us?",
                 "Summarise the input files for me", "Add a task to …"];
function chatMd(t) {   // light Markdown: bold, inline code, bullets, line breaks
  return esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")
    .split("\n").map(l => /^\s*[-*]\s+/.test(l) ? `<li>${l.replace(/^\s*[-*]\s+/, "")}</li>` : l).join("\n")
    .replace(/(<li>.*?<\/li>\n?)+/gs, m => `<ul>${m.replace(/\n/g, "")}</ul>`).replace(/\n/g, "<br>");
}
function chatMsgHtml(m) {
  const p = principalAgent() || { name: "Sol", color: "#fbbf24", avatar: "🧭" };
  if (m.role === "user") return `<div class="cmsg me" data-i="${m.i}"><div class="ctext">${chatMd(m.text)}</div><span class="ctime">${esc((m.t || "").slice(11, 16))}</span></div>`;
  const done = (m.done || []).map(d => `<span class="cact">${esc(d)}</span>`).join("");
  const pend = (m.pending || []).map((x, k) => {
    const res = x.resolved ? `<span class="cres">${x.resolved === "declined" ? "✕ declined" : "✓ " + esc(x.resolved)}</span>` : "";
    const btns = x.resolved ? "" : `<button class="btn sm go" data-ok="${k}">${x.kind === "recruit" ? "✓ Approve recruit" : "▶ Start it"}</button><button class="btn sm" data-no="${k}">Not now</button>`;
    if (x.kind === "recruit") {
      const s = x.spec || {}, face = { id: s.name, name: s.name, role: s.role, color: s.color || "#5ee6c4", avatar: s.avatar || "🤖", look: s.look };
      return `<div class="cpend"><span class="av">${avatarSVG(face, true)}</span><div><span class="new">PROPOSED RECRUIT</span><br><b>${esc(s.name)}</b> · ${esc(s.role || "")} · ${esc(s.model || "sonnet")}
        <div class="hint">${esc(s.why || s.personality || "")}</div><div class="crow">${btns}${res}</div></div></div>`;
    }
    return `<div class="cpend"><span class="ic" style="font-size:22px">▶</span><div><b>${x.autopilot ? "Start Autopilot?" : "Start a sprint?"}</b>
      <div class="hint">Sol hands out the next tasks and the team gets to work.</div><div class="crow">${btns}${res}</div></div></div>`;
  }).join("");
  return `<div class="cmsg sol" data-i="${m.i}"><span class="av">${avatarSVG(p, true, { badge: false })}</span>
    <div class="cbody"><div class="ctext">${chatMd(m.text)}</div>${done ? `<div class="cacts">${done}</div>` : ""}${pend}
    <span class="ctime">${esc((m.t || "").slice(11, 16))}${m.cost_usd ? ` · ≈ ${money(m.cost_usd)}` : ""}</span></div></div>`;
}
async function renderChat(force) {
  if (!S.cur) return;
  const p = principalAgent();
  if (p) { $("chatAv").innerHTML = avatarSVG(p, awake(p)); $("chatName").textContent = `${p.name} ${p.avatar}`; }
  if (S.chat.slug !== S.cur || force) {
    try { S.chat = { slug: S.cur, messages: (await api("GET", `/api/projects/${S.cur}/chat`)).messages }; } catch (e) { return; }
  }
  const log = $("chatLog"), proj = S.projects.find(x => x.slug === S.cur);
  log.innerHTML = S.chat.messages.map(chatMsgHtml).join("") || `<div class="chat-empty"><span class="av">${p ? avatarSVG(p, true) : ""}</span>
    <b>Hi! I'm ${esc(p ? p.name : "Sol")}, your Principal on “${esc(proj ? proj.name : "")}”.</b>
    <span class="hint">Ask me how things are going, change priorities, add or move tasks, bench or bring back teammates, or ask me to look something up in the project's files. I'll do it and tell you what I changed.</span></div>`;
  log.querySelectorAll("[data-ok],[data-no]").forEach(b => b.onclick = async () => {
    const i = +b.closest(".cmsg").dataset.i, approve = b.hasAttribute("data-ok"), which = +(b.dataset.ok ?? b.dataset.no);
    try { await api("POST", `/api/projects/${S.cur}/chat/${i}/resolve`, { which, approve }); } catch (e) { toast(e.message); }
  });
  $("chatSugg").innerHTML = (S.chat.messages.length < 2 ? SUGGEST : SUGGEST.slice(0, 3)).map(s => `<button type="button" class="sugg">${esc(s)}</button>`).join("");
  $("chatSugg").querySelectorAll(".sugg").forEach(b => b.onclick = () => {
    const t = $("chatText"); t.value = b.textContent.replace("…", ""); t.focus(); if (!b.textContent.endsWith("…")) $("chatForm").requestSubmit();
  });
  renderChatThinking();
  log.scrollTop = log.scrollHeight;
}
function renderChatThinking() {
  const p = principalAgent(), box = $("chatThink"); if (!p || !box) return;
  const last = S.chat.messages[S.chat.messages.length - 1];
  const waiting = S.chat.slug === S.cur && last && last.role === "user";
  box.hidden = !waiting;
  if (!waiting) return;
  if (!box._av) { $("chatThinkAv").innerHTML = avatarSVG(p, true, { badge: false }); box._av = true; }
  const live = awake(p) ? p : { ...p, live: { step: S.chatWaitNote || "Reading your message", progress: null } };
  setBubble($("chatThinkBubble"), live);
  const log = $("chatLog"); log.scrollTop = log.scrollHeight;
}
$("chatForm").onsubmit = async e => {
  e.preventDefault();
  const t = $("chatText"), text = t.value.trim(); if (!text || !S.cur) return;
  t.value = ""; t.style.height = "";
  S.chatWaitNote = null;
  try { await api("POST", `/api/projects/${S.cur}/chat`, { text }); } catch (err) { toast(err.message); t.value = text; }
};
$("chatText").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("chatForm").requestSubmit(); } });
$("chatText").addEventListener("input", e => { const t = e.target; t.style.height = ""; t.style.height = Math.min(t.scrollHeight, 180) + "px"; });
$("chatAttach").onclick = () => $("fileInput").click();
$("chatProfile").onclick = () => { const p = principalAgent(); if (p) openDrawer(p.id); };
function onChatEvent(ev) {
  if (ev.slug !== S.cur) return;
  if (ev.type === "chat") {
    if (S.chat.slug === S.cur && !S.chat.messages.some(m => m.i === ev.message.i)) S.chat.messages.push(ev.message);
    if (ev.message.role === "principal" && S.view !== "chat") { S.chatUnread++; renderCounts(); toast(`💬 ${(principalAgent() || {}).name || "Sol"} replied`); }
  } else if (ev.type === "chat_update") {
    const k = S.chat.messages.findIndex(m => m.i === ev.message.i); if (k >= 0) S.chat.messages[k] = ev.message;
  } else if (ev.type === "chat_wait") {
    S.chatWaitNote = "Finishing the sprint plan first, then I'll answer you";
  }
  if (S.view === "chat") renderChat(); else renderChatThinking();
}
function renderCrew() {   // everyone working right now, across all projects
  const busy = S.agents.filter(awake);
  $("sbCrew").innerHTML = busy.length ? `<span class="lbl">Working now</span>` + busy.map(a =>
    `<span class="av" data-aid="${esc(a.id)}" title="${esc(a.name)} · ${esc((a.live || {}).step || "")}">${avatarSVG(a, true)}</span>`).join("") : "";
  $("sbCrew").querySelectorAll("[data-aid]").forEach(el => el.onclick = () => openDrawer(el.dataset.aid));
}
function renderMemory() {
  const ctx = (S.proj && S.proj.context) || "";
  $("memory").innerHTML = ctx ? mdDoc(ctx) : `<span class="hint">No project selected.</span>`;
  renderFiles();
}
async function renderFiles() {   // the project's inputs/ folder, shown above the memory document
  const el = $("inputFiles"); if (!el) return;
  if (!S.cur) { el.innerHTML = ""; return; }
  let r; try { r = await api("GET", `/api/projects/${S.cur}/files`); } catch (e) { return; }
  const total = r.files.reduce((s, f) => s + f.size, 0);
  el.innerHTML = `<div class="files-h"><b>📎 Input files</b><span class="hint">${r.files.length ? `${r.files.length} file${r.files.length > 1 ? "s" : ""} · ${fmtSize(total)}` : "none yet"} · <code>inputs/</code></span>
      <button class="btn sm" id="filesAdd">＋ Add files</button><button class="btn sm" id="filesOpen" title="${esc(r.folder)}">Open folder</button></div>
    ${r.files.length ? `<div class="files-grid">${r.files.map(f => `<div class="fcard" title="${esc(f.name)}"><span>${FILE_ICON[f.kind] || "📦"}</span>
      <div><b>${esc(f.name)}</b><i>${esc(f.kind)} · ${fmtSize(f.size)}</i></div></div>`).join("")}</div>`
      : `<div class="hint" style="padding:6px 2px">Drop files anywhere in the window, or use ＋ Add files. Any size: big raw data is fine.</div>`}`;
  $("filesAdd").onclick = () => $("fileInput").click();
  $("filesOpen").onclick = () => api("POST", `/api/projects/${S.cur}/files/open`).catch(e => toast(e.message));
}
document.body.insertAdjacentHTML("beforeend", `<input type="file" id="fileInput" multiple hidden>`);
$("fileInput").onchange = e => { addFilesToProject(e.target.files); e.target.value = ""; };
function mdDoc(text) {   // headings, bullets, bold, inline code; HTML comments dropped
  return text.replace(/<!--[\s\S]*?-->/g, "").split("\n").reduce((acc, line) => {
    const h = line.match(/^(#{1,3})\s+(.*)$/);
    let html = esc(line).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>").replace(/(^|\s)_(.+?)_(\s|$)/g, "$1<i>$2</i>$3");
    if (h) { acc.close(); acc.out += `<h${h[1].length}>${esc(h[2])}</h${h[1].length}>`; }
    else if (/^\s*[-*]\s+/.test(line)) { if (!acc.list) { acc.out += "<ul>"; acc.list = true; } acc.out += `<li>${html.replace(/^\s*[-*]\s+/, "")}</li>`; }
    else { acc.close(); if (line.trim()) acc.out += `<p>${html}</p>`; }
    return acc;
  }, { out: "", list: false, close() { if (this.list) { this.out += "</ul>"; this.list = false; } } }).out;
}

// ---------------------------------------------------------------- header
function renderCli() {
  const b = $("cliBadge"), c = S.cli || {};
  b.className = "cli " + (c.ok ? "ok" : c.checked ? "bad" : "");
  b.querySelector(".txt").textContent = c.ok ? "Connected via Claude Subscription (CLI)" : c.checked ? "Claude CLI not found" : "Checking CLI…";
  b.title = c.ok ? `${c.version} · ${c.path}\nEvery agent turn runs as a local \`claude -p\` process on your subscription. No API key.` : (c.error || "");
}
function projectBusy(p) {
  return (p.sprint && p.sprint.running) || (p.team || []).some(id => awake(agentById(id)));
}
function renderTabs() {   // the project list in the sidebar
  $("projList").innerHTML = S.projects.map(p => {
    const working = (p.team || []).filter(id => awake(agentById(id))).length;
    return `<button class="${p.slug === S.cur ? "on" : ""}" data-slug="${esc(p.slug)}" title="${esc(p.name)}">
      <span class="live ${projectBusy(p) ? "on" : ""}"></span><span class="pname">${esc(p.name)}</span>
      <span class="n">${working ? `${working} ⚡` : (p.team || []).length + " 👥"}</span></button>`;
  }).join("") || `<span class="hint" style="padding:4px 10px">No projects yet</span>`;
  $("projList").querySelectorAll("button").forEach(b => b.onclick = () => setProject(b.dataset.slug));
  renderCrew(); renderCounts();
}

// ---------------------------------------------------------------- empty state
function renderEmpty() {
  const none = !S.projects.length;
  $("empty").hidden = !none; $("mhead").hidden = none;
  setView(S.view);
  if (!none) return;
  const crew = S.agents.slice(0, 6).map(a => `<div class="av">${avatarSVG(a, a.kind === "principal")}</div>`).join("");
  $("empty").innerHTML = `<div class="hero"><div class="crew">${crew}</div>
    <h1>Your studio is ready</h1>
    <p>Describe what you want to build. ${esc((principalAgent() || {}).name || "The Principal")} will review the roster, draft the right agents,
    suggest new specialists if skills are missing, and set up the project's shared memory. Nothing starts until you approve the team.</p>
    <button class="btn primary" onclick="openBrief()">＋ Start your first project</button></div>`;
}

// ---------------------------------------------------------------- bubbles & cards
function bubbleHtml(a) {
  const L = a.live || {}, p = L.progress;
  const pct = p ? Math.round(100 * p.cur / Math.max(1, p.total)) : 0;
  return `<div class="step">${L.step ? esc(L.step) : "Thinking"}<span class="typing"><i></i><i></i><i></i></span></div>
    ${L.task ? `<div class="task">${esc(L.task)}</div>` : ""}
    <div class="pbar ${p ? "" : "indet"}"><i style="width:${pct}%"></i></div>
    <div class="plabel"><span>${p ? esc(p.label || "progress") : "working…"}</span><b>${p ? `${p.cur}/${p.total} completed` : ""}</b></div>`;
}
// update a thought bubble in place: no re-creation, so the progress bar glides and nothing flickers
function setBubble(b, a) {
  if (!b) return;
  if (!b._ready) {
    b.innerHTML = `<div class="step"><span class="st"></span><span class="typing"><i></i><i></i><i></i></span></div><div class="task"></div>
      <div class="pbar"><i></i></div><div class="plabel"><span class="pl"></span><b class="pc"></b></div>`;
    b._ready = true;
  }
  const L = a.live || {}, p = L.progress, pct = p ? Math.round(100 * p.cur / Math.max(1, p.total)) : 0;
  const set = (sel, txt) => { const el = b.querySelector(sel); if (el.textContent !== txt) el.textContent = txt; };
  set(".st", L.step || "Thinking");
  set(".task", L.task || ""); b.querySelector(".task").hidden = !L.task;
  const bar = b.querySelector(".pbar"); bar.classList.toggle("indet", !p);
  bar.querySelector("i").style.width = p ? pct + "%" : "";
  set(".pl", p ? (p.label || "progress") : "working…");
  set(".pc", p ? `${p.cur}/${p.total} completed` : "");
}
function zzz() { return `<div class="zzz"><span>z</span><span>z</span><span>Z</span></div>`; }
function stateText(a) {
  const L = a.live || {};
  if (L.state === "working") return "● working";
  if (L.state === "thinking") return "● thinking";
  if (L.error) return `⚠ ${L.error.slice(0, 60)}`;
  return "asleep";
}
function agentCard(a) {
  const on = awake(a);
  return `<div class="agent ${on ? "working" : ""}" data-aid="${esc(a.id)}" style="--c:${esc(a.color)}" title="Open ${esc(a.name)}">
    <div class="bubble">${on ? bubbleHtml(a) : ""}</div>
    <div class="av">${avatarSVG(a, on)}${on ? "" : zzz()}</div>
    <div class="name">${esc(a.name)}</div><div class="role">${esc(a.role)}</div>
    <div class="badges"><span class="teambadge">on team</span><span class="chip model">${esc(a.model)}</span></div>
    <div class="state">${esc(stateText(a))}</div></div>`;
}
function seat(a) {
  const on = awake(a);
  return `<div class="seat ${on ? "working" : ""}" data-aid="${esc(a.id)}" style="--c:${esc(a.color)}" title="${esc(a.name)} · ${esc(a.role)}">
    <div class="av">${avatarSVG(a, on)}${on ? "" : zzz()}</div><b>${esc(a.name)}</b><span class="hint">${esc(a.role)}</span></div>`;
}
function mini(a) {
  const on = awake(a);
  return `<div class="mini ${on ? "working" : ""}" data-aid="${esc(a.id)}" style="--c:${esc(a.color)}">
    <div class="av">${avatarSVG(a, on)}</div><div><b>${esc(a.name)}</b><span class="hint">🔒 ${esc(a.project_name || a.project)}${on ? " · working" : ""}</span></div></div>`;
}

// ---------------------------------------------------------------- floor
function renderFloor() {
  const proj = S.projects.find(p => p.slug === S.cur);
  const team = proj ? (proj.team || []).map(agentById).filter(Boolean) : [];
  const members = S.agents.filter(a => a.kind !== "principal");
  const elsewhere = members.filter(a => a.project && a.project !== S.cur);
  const bench = members.filter(a => !a.project);
  renderRoom(team);
  $("activeHint").textContent = proj ? `${proj.name} · ${team.length} agent${team.length === 1 ? "" : "s"}` : "";
  $("elseZone").hidden = !elsewhere.length;
  $("elsewhere").innerHTML = elsewhere.map(mini).join("");
  $("bench").innerHTML = bench.map(seat).join("") || `<span class="hint" style="padding:10px">Everyone is on a team.</span>`;
  document.querySelectorAll("#view-floor [data-aid]").forEach(el => el.onclick = () => openDrawer(el.dataset.aid));
  // drag & drop: Sideline seats -> the studio room (draft), walkers in the room -> Sideline (bench)
  document.querySelectorAll("#walkers .walker, #bench .seat").forEach(el => {
    el.draggable = true;
    el.ondragstart = e => {
      const from = el.classList.contains("seat") ? "bench" : "roster";
      const r = el.getBoundingClientRect();
      try { e.dataTransfer.setDragImage(el, r.width / 2, from === "bench" ? r.height * .55 : r.height); } catch (err) { /* older engines */ }
      e.dataTransfer.setData("application/x-agent", JSON.stringify({ id: el.dataset.aid, from }));
      e.dataTransfer.effectAllowed = "move";
      S.dragFrom = from; el.classList.add("dragging");
      document.body.classList.add(from === "bench" ? "drag-to-roster" : "drag-to-bench");
    };
    el.ondragend = () => { el.classList.remove("dragging"); S.dragFrom = null; document.body.classList.remove("drag-to-roster", "drag-to-bench"); };
  });
  document.querySelectorAll("#elsewhere .mini").forEach(el => {   // locked to another project: not draggable
    el.draggable = true;
    el.ondragstart = e => { e.preventDefault(); toast(`${(agentById(el.dataset.aid) || {}).name} is on another project. Move them to the Sideline there first.`); };
  });
  renderPrincipal();
}
// ---------------------------------------------------------------- the studio room: team members walk around; workers sit at desks
// The art (1792 × 768) covers the whole room edge to edge; ay() converts a height on the art into a height in the room,
// so desks and the walkable floor line up with the painting whatever the window size.
const WALK = {};        // agent id -> { x, y, timer, desk } (percent of the room; y = feet position)
const ART = { w: 1792, h: 768, posY: .72 };
function artBox() {
  const r = $("room").getBoundingClientRect(), s = Math.max(r.width / ART.w, r.height / ART.h);
  return { W: r.width || 1, H: r.height || 1, ih: ART.h * s, oy: (r.height - ART.h * s) * ART.posY };
}
function ay(p) { const m = artBox(); return (m.oy + p / 100 * m.ih) / m.H * 100; }
function floorBox() { return { x0: 7, x1: 93, y0: ay(72), y1: Math.min(ay(97), 95) }; }
function deskY() { return ay(64); }
function deskSpots(n) {   // desk x positions along the back wall
  const k = Math.max(n, 3);
  return Array.from({ length: k }, (_, i) => 10 + (80 * (i + .5)) / k);
}
function depth(y) { const f = floorBox(); return (0.84 + 0.26 * (y - f.y0) / Math.max(1, f.y1 - f.y0)).toFixed(3); }   // further back = smaller
function walkerHtml(a) {
  return `<div class="walker" data-aid="${esc(a.id)}" style="--c:${esc(a.color)};--pants:${pantsColor(a)}" title="${esc(a.name)} · ${esc(a.role)}">
    <div class="bubble"></div>
    <div class="fx"><span>{ }</span><span>&lt;/&gt;</span><span>✦</span><span>01</span><span>✓</span></div>
    <div class="wbody"><div class="whead av">${headSVG(a, true)}</div><div class="wtorso">${bodySVG(a)}</div><div class="legs"><i></i><i></i></div></div>
    <div class="shadow"></div><div class="tag">${esc(a.avatar)} ${esc(a.name)}</div></div>`;
}
function deskSVG(i) {
  const K = "#2a1d33";
  return `<svg viewBox="0 0 120 74" aria-hidden="true">
    <defs><clipPath id="scr${i}"><rect x="12" y="8" width="44" height="28" rx="2"/></clipPath></defs>
    <ellipse class="scr-glow" cx="34" cy="22" rx="36" ry="26" fill="var(--c)" style="filter:blur(9px)"/>
    <rect x="30" y="38" width="8" height="8" fill="#4b4560" stroke="${K}" stroke-width="2"/>
    <rect x="22" y="44" width="24" height="4" rx="2" fill="#4b4560" stroke="${K}" stroke-width="2"/>
    <rect x="8" y="4" width="52" height="36" rx="4" fill="#3b3550" stroke="${K}" stroke-width="2.6"/>
    <rect class="scr" x="12" y="8" width="44" height="28" rx="2" fill="#1b1f33"/>
    <g clip-path="url(#scr${i})"><g class="code" stroke-width="2.4" stroke-linecap="round">
      ${[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map(k => `<path d="M${16 + (k % 3) * 4} ${12 + k * 6} h${[22, 30, 14, 26, 18, 32, 12, 24, 20, 28][k]}" stroke="${k % 4 === 1 ? "#fde68a" : k % 4 === 2 ? "#a7f3d0" : "rgba(255,255,255,.55)"}"/>`).join("")}
    </g></g>
    <rect x="2" y="46" width="116" height="9" rx="3" fill="#c98b4e" stroke="${K}" stroke-width="2.6"/>
    <rect x="2" y="46" width="116" height="3" rx="1.5" fill="#e3a868"/>
    <path d="M68 46 L72 22 L102 22 L106 46 Z" fill="#c9cbd6" stroke="${K}" stroke-width="2.4" stroke-linejoin="round"/>
    <circle class="logo" cx="87" cy="33" r="3.4" fill="#9a9cab"/>
    <rect x="62" y="44" width="50" height="4" rx="2" fill="#a9abb8" stroke="${K}" stroke-width="2"/>
    <rect x="12" y="38" width="9" height="9" rx="2" fill="#f4f1ea" stroke="${K}" stroke-width="1.8"/>
    <rect x="8" y="55" width="7" height="18" fill="#8a5a32" stroke="${K}" stroke-width="2.2"/><rect x="105" y="55" width="7" height="18" fill="#8a5a32" stroke="${K}" stroke-width="2.2"/>
    <rect x="22" y="55" width="30" height="13" rx="2" fill="#a86f3e" stroke="${K}" stroke-width="2.2"/><circle cx="37" cy="61.5" r="1.8" fill="${K}"/>
  </svg>`;
}
function renderRoom(team) {
  const wk = $("walkers");
  $("roomEmpty").hidden = team.length > 0;
  const spots = deskSpots(team.length), dy = deskY();
  $("deskRow").innerHTML = spots.map((x, i) => {
    const a = team[i];
    return `<div class="desk ${a && awake(a) ? "on" : ""}" data-desk="${a ? esc(a.id) : ""}" style="left:${x}%;top:${dy}%;--c:${esc(a ? a.color : "#555")}">
      ${deskSVG(i)}${a ? `<span class="plate">${esc(a.name)}</span>` : ""}</div>`;
  }).join("");
  const ids = new Set(team.map(a => a.id));
  wk.querySelectorAll(".walker").forEach(el => { if (!ids.has(el.dataset.aid)) { clearTimeout((WALK[el.dataset.aid] || {}).timer); delete WALK[el.dataset.aid]; el.remove(); } });
  const f = floorBox();
  team.forEach((a, i) => {
    let el = wk.querySelector(`.walker[data-aid="${CSS.escape(a.id)}"]`);
    const desk = spots[i];
    if (!el) {
      wk.insertAdjacentHTML("beforeend", walkerHtml(a));
      el = wk.lastElementChild;
      const spawn = (S.spawn || {})[a.id];   // dropped from the Sideline: appear exactly where you let go
      if (spawn) delete S.spawn[a.id];
      const w = WALK[a.id] = { x: spawn ? spawn.x : desk, y: spawn ? spawn.y : f.y0 + 3, timer: null, desk };
      place(el, w.x, w.y, 0);
      landBounce(el);
      w.timer = setTimeout(() => wander(a.id), spawn ? 2600 : 400 + Math.random() * 1200);
    }
    WALK[a.id].desk = desk;
    updateWalker(el, a);
  });
}
function landBounce(el) { el.classList.remove("landed"); void el.offsetWidth; el.classList.add("landed"); setTimeout(() => el.classList.remove("landed"), 700); }
function place(el, x, y, ms) {
  el.style.transitionDuration = `${ms}ms`;
  el.style.left = x + "%"; el.style.top = y + "%";
  el.style.setProperty("--s", depth(y)); el.style.zIndex = Math.round(y * 10);
  const W = $("room").getBoundingClientRect().width;   // keep the thought bubble inside the room near the walls
  el.style.setProperty("--bx", (x < 16 ? (16 - x) : x > 84 ? -(x - 84) : 0) * W / 100 + "px");
}
function walkTo(id, x, y, done) {
  const el = document.querySelector(`#walkers .walker[data-aid="${CSS.escape(id)}"]`), w = WALK[id];
  if (!el || !w) return;
  const room = $("room").getBoundingClientRect();
  const dist = Math.hypot((x - w.x) * room.width / 100, (y - w.y) * room.height / 100);
  const ms = Math.max(350, dist / 70 * 1000);    // ~70 px per second: a brisk cartoon stroll
  if (Math.abs(x - w.x) > .5) el.classList.toggle("left", x < w.x);
  el.classList.add("walking"); el.classList.remove("seated");
  place(el, x, y, ms);
  w.x = x; w.y = y;
  clearTimeout(w.timer);
  w.timer = setTimeout(() => { el.classList.remove("walking"); done && done(); }, ms);
}
function wander(id) {
  const a = agentById(id), w = WALK[id]; if (!a || !w) return;
  if (awake(a)) return goToDesk(id);
  const f = floorBox();
  // short hops, not cross-room dashes: pick a spot within ~30% of where they are
  const x = Math.max(f.x0, Math.min(f.x1, w.x + (Math.random() - .5) * 60));
  const y = f.y0 + Math.random() * (f.y1 - f.y0);
  walkTo(id, x, y, () => { w.timer = setTimeout(() => wander(id), 1800 + Math.random() * 4200); });
}
function goToDesk(id) {
  const w = WALK[id], el = document.querySelector(`#walkers .walker[data-aid="${CSS.escape(id)}"]`); if (!w || !el) return;
  if (el.classList.contains("seated") || w.heading) return;
  w.heading = true;
  const room = $("room").getBoundingClientRect(), wpx = parseFloat(getComputedStyle(el).width) || 60;
  const seatX = w.desk + (0.44 * wpx / room.width) * 100;   // behind the laptop on the right half of the desk
  walkTo(id, seatX, deskY() - 0.4, () => {
    w.heading = false;
    if (awake(agentById(id))) { el.classList.add("seated"); el.classList.remove("left"); el.style.zIndex = 395; } else wander(id);
  });
}
function updateWalker(el, a) {
  const on = awake(a), w = WALK[a.id];
  el.classList.toggle("working", on);
  if (on) setBubble(el.querySelector(".bubble"), a);
  const desk = document.querySelector(`.desk[data-desk="${CSS.escape(a.id)}"]`);
  if (desk) desk.classList.toggle("on", on);
  if (!w) return;
  if (on) goToDesk(a.id);
  else if (el.classList.contains("seated")) {   // task done: stand up, stretch, wander off
    el.classList.remove("seated"); clearTimeout(w.timer);
    place(el, w.x, floorBox().y0 + 2, 500); w.y = floorBox().y0 + 2;
    w.timer = setTimeout(() => wander(a.id), 1200);
  }
}
// keep everything aligned with the art when the window is resized
new ResizeObserver(() => {
  const proj = S.projects.find(p => p.slug === S.cur);
  if (!proj) return;
  renderRoom((proj.team || []).map(agentById).filter(Boolean));
  const f = floorBox();
  Object.entries(WALK).forEach(([id, w]) => {
    const el = document.querySelector(`#walkers .walker[data-aid="${CSS.escape(id)}"]`); if (!el || el.classList.contains("seated")) return;
    w.y = Math.max(f.y0, Math.min(f.y1, w.y)); place(el, w.x, w.y, 0);
  });
}).observe($("room"));

// ---------------------------------------------------------------- drag & drop (Sideline <-> room, and moving people inside the room)
function roomPoint(e) {   // cursor -> a spot on the walkable floor, in room percent
  const r = $("room").getBoundingClientRect(), f = floorBox();
  return { x: Math.max(f.x0, Math.min(f.x1, (e.clientX - r.left) / r.width * 100)),
           y: Math.max(f.y0, Math.min(f.y1, (e.clientY - r.top) / r.height * 100)) };
}
function setupDropZones() {
  const roomZone = document.querySelector("#view-floor .zone-active"), benchZone = document.querySelector("#view-floor .zone-bench");
  const accept = (zone, ok) => {
    zone.addEventListener("dragover", e => {
      if (!e.dataTransfer.types.includes("application/x-agent") || !ok(S.dragFrom)) return;
      e.preventDefault(); e.dataTransfer.dropEffect = "move"; zone.classList.add("drop-over");
      if (zone === roomZone) showDropMarker(e);
    });
    zone.addEventListener("dragleave", e => { if (!zone.contains(e.relatedTarget)) { zone.classList.remove("drop-over"); hideDropMarker(); } });
  };
  accept(roomZone, f => f === "bench" || f === "roster");
  accept(benchZone, f => f === "roster");
  roomZone.addEventListener("drop", async e => {
    roomZone.classList.remove("drop-over"); hideDropMarker();
    let d; try { d = JSON.parse(e.dataTransfer.getData("application/x-agent")); } catch (err) { return; }
    if (!d) return;
    e.preventDefault();
    const a = agentById(d.id), pt = roomPoint(e); if (!a) return;
    if (d.from === "roster") {   // moved inside the room: put them down right there
      const w = WALK[d.id], el = document.querySelector(`#walkers .walker[data-aid="${CSS.escape(d.id)}"]`);
      if (!w || !el) return;
      clearTimeout(w.timer); el.classList.remove("walking", "seated"); w.heading = false;
      w.x = pt.x; w.y = pt.y; place(el, pt.x, pt.y, 0); landBounce(el);
      w.timer = setTimeout(() => wander(d.id), 3000);
      return;
    }
    if (!S.cur) return toast("Create or pick a project first.");
    try {
      S.spawn = S.spawn || {}; S.spawn[d.id] = pt;
      await api("POST", `/api/agents/${d.id}/assign`, { slug: S.cur });
      toast(`${a.name} joined the team ${a.avatar}`);
      await load();
    } catch (err) { delete (S.spawn || {})[d.id]; toast(err.message); }
  });
  benchZone.addEventListener("drop", async e => {
    benchZone.classList.remove("drop-over");
    let d; try { d = JSON.parse(e.dataTransfer.getData("application/x-agent")); } catch (err) { return; }
    if (!d || d.from !== "roster") return;
    e.preventDefault();
    const a = agentById(d.id); if (!a) return;
    try { await api("POST", `/api/agents/${d.id}/release`); toast(`${a.name} is resting on the Sideline`); await load(); }
    catch (err) { toast(err.message); }
  });
}
function showDropMarker(e) {
  let m = $("dropMarker");
  if (!m) { $("room").insertAdjacentHTML("beforeend", `<div id="dropMarker" class="drop-marker"></div>`); m = $("dropMarker"); }
  const pt = roomPoint(e); m.style.left = pt.x + "%"; m.style.top = pt.y + "%"; m.hidden = false;
}
function hideDropMarker() { const m = $("dropMarker"); if (m) m.hidden = true; }
setupDropZones();
function renderHeader() {   // project title, stats and sprint controls: visible in every view
  const proj = S.proj && S.proj.project, el = $("mhead");
  if (!proj) { el.innerHTML = ""; return; }
  const sp = proj.sprint || {}, busy = sp.running || (proj.team || []).some(id => awake(agentById(id)));
  el.innerHTML = `
    <div class="title"><b>${esc(proj.name)}</b><span class="hint">created ${esc(proj.created)} · <code>projects/${esc(proj.slug)}</code></span></div>
    <div class="pmeta"><span class="chip">👥 ${(proj.team || []).length} on team</span><span class="chip">🧾 ${proj.runs || 0} turns</span>
      <span class="chip" title="Claude Code's API-equivalent estimate; billed to your subscription, not per use">≈ ${money(proj.cost_usd)} usage</span></div>
    <div class="acts">
      <button class="btn go" id="sprintBtn" ${sp.running ? "disabled" : ""} title="The Principal assigns Up Next tasks to idle teammates, who work in parallel">▶ Run sprint</button>
      <button class="btn" id="autoBtn" ${sp.running ? "disabled" : ""} title="Keep planning and running sprints until the board is done (max ${S.settings.autopilot_cycles} cycles)">🤖 Autopilot</button>
      <button class="btn danger" id="stopBtn" ${busy ? "" : "disabled"}>■ Stop</button>
      <button class="btn" id="filesBtn" title="Add files to this project's inputs/ folder (any size; you can also drop files anywhere in the window)">📎 Add files</button>
      <button class="btn" id="restaffBtn" title="Give the Principal a new brief to add agents">↻ Re-staff</button></div>`;
  $("filesBtn").onclick = () => $("fileInput").click();
  $("sprintBtn").onclick = () => startSprint(false);
  $("autoBtn").onclick = () => startSprint(true);
  $("stopBtn").onclick = async () => { await api("POST", `/api/projects/${S.cur}/stop`); toast("Stopping the team…"); };
  $("restaffBtn").onclick = () => openBrief(S.cur);
}
function renderPrincipal() {   // the Principal's desk: built once, then updated in place (no flicker while Sol thinks)
  const p = principalAgent(), el = $("principalDesk");
  if (!p) { el.innerHTML = `<span class="hint">No Principal profile found (.agents/principal.md).</span>`; el._pid = null; return; }
  if (el._pid !== p.id) {
    el.innerHTML = `<div class="pdesk-av" data-aid="${esc(p.id)}" style="--c:${esc(p.color)}"><div class="pav"></div></div>
      <div class="who"><b>${esc(p.name)} ${esc(p.avatar)}</b> <span class="chip">Principal · oversees every project</span>
        <button class="btn sm talk" id="talkSol">💬 Talk to ${esc(p.name)}</button><div class="sprint-note"></div></div>
      <div class="bubble pbubble" style="--c:${esc(p.color)}"></div>`;
    el._pid = p.id; el._on = null;
    el.querySelector(".pdesk-av").onclick = () => setView("chat");
    el.querySelector("#talkSol").onclick = () => setView("chat");
  }
  const proj = S.proj && S.proj.project, on = awake(p), sp = (proj && proj.sprint) || {};
  if (el._on !== on) {   // swap the face only when Sol wakes up or falls asleep
    el.querySelector(".pav").innerHTML = avatarSVG(p, on) + (on ? "" : zzz());
    el._on = on;
  }
  el.classList.toggle("working", on);
  el.querySelector(".pdesk-av").classList.toggle("working", on);
  setBubble(el.querySelector(".pbubble"), p);
  const note = el.querySelector(".sprint-note");
  const txt = sp.note ? `${sp.autopilot ? "Autopilot" : "Sprint"}${sp.cycle ? ` · cycle ${sp.cycle}/${sp.cycles}` : ""}: ${sp.note}`
    : on ? "" : "Resting. Press ▶ Run sprint and I'll hand out the next tasks.";
  if (note.textContent !== txt) note.textContent = txt;
  note.classList.toggle("run", !!sp.running);
  renderHeader();
}
async function startSprint(autopilot) {
  try { await api("POST", `/api/projects/${S.cur}/sprint`, { autopilot }); toast(autopilot ? "Autopilot on: the Principal is planning…" : "Sprint started: the Principal is planning…"); }
  catch (e) { toast(e.message); }
}

// live patch of one agent everywhere it appears (no full re-render, so animations keep running)
function patchAgent(id) {
  const a = agentById(id); if (!a) return;
  const on = awake(a);
  if (a.kind === "principal") { renderPrincipal(); renderChatThinking(); }
  document.querySelectorAll(`[data-aid="${CSS.escape(id)}"]`).forEach(el => {
    if (el.classList.contains("walker")) return updateWalker(el, a);   // in the studio room: walk to the desk / back to strolling
    if (el.classList.contains("pdesk-av")) return;                      // handled by renderPrincipal (in place)
    const was = el.classList.contains("working");
    el.classList.toggle("working", on);
    const av = el.querySelector(".av") || (el.classList.contains("pdesk-av") ? el : null);
    if (was !== on && av) {
      const bubble = el.classList.contains("pdesk-av") ? el.querySelector(".bubble") : null;
      if (el.classList.contains("pdesk-av")) { renderPrincipal(); return; }
      av.innerHTML = avatarSVG(a, on) + (on || el.classList.contains("mini") ? "" : zzz());
      if (bubble) el.appendChild(bubble);
    }
    const b = el.querySelector(".bubble");
    if (b && on) setBubble(b, a);
    const st = el.querySelector(".state"); if (st) st.textContent = stateText(a);
  });
  renderTabs(); patchCards(id);
  if (S.drawer === id) renderDrawerLive();
  if (a.kind === "principal") setBubble(document.querySelector("#modal .thinking .bubble"), a);
  const stop = $("stopBtn");
  if (stop && S.proj) { const p = S.projects.find(x => x.slug === S.cur); stop.disabled = !(p && ((p.sprint || {}).running || (p.team || []).some(x => awake(agentById(x))))); }
}

// ---------------------------------------------------------------- board
const seenCards = new Set();
function cardHtml(t) {
  const a = agentById(t.assignee), live = a && awake(a) && t.column === "In Progress";
  const p = live && a.live.progress, pct = p ? Math.round(100 * p.cur / Math.max(1, p.total)) : 0;
  const key = `${S.cur}:${t.id}:${t.column}`, fresh = !seenCards.has(key); seenCards.add(key);   // animate only cards that just appeared/moved
  return `<div class="card ${live ? "live" : ""} ${fresh ? "fresh" : ""}" draggable="true" data-tid="${esc(t.id)}" data-who="${esc(t.assignee || "")}" style="--c:${esc(a ? a.color : "#8f8da3")}">
    <button class="x" title="Remove task" data-del="${esc(t.id)}">✕</button>
    <div class="tid">${esc(t.id)}</div><div class="t">${esc(t.title)}</div>
    <div class="who">${a ? `<span class="av">${avatarSVG(a, live)}</span>${esc(a.name)}` : "unassigned"}</div>
    ${live ? `<div class="mini-p"><i style="width:${p ? pct : 12}%"></i></div>` : ""}
    ${t.note ? `<div class="note">${esc(t.note)}</div>` : ""}</div>`;
}
function renderBoard() {
  const tasks = (S.proj && S.proj.tasks) || [];
  $("kanban").innerHTML = COLS.map(c => {
    const list = tasks.filter(t => t.column === c);
    return `<div class="col" data-col="${c}"><div class="col-h"><span class="pip" style="background:${COLC[c]}"></span>${c}<span class="n">${list.length}</span></div>
      <div class="col-b">${list.map(cardHtml).join("")}</div></div>`;
  }).join("");
  document.querySelectorAll("#kanban .card").forEach(card => {
    card.ondragstart = e => { e.dataTransfer.setData("text/plain", card.dataset.tid); card.style.opacity = .4; };
    card.ondragend = () => card.style.opacity = 1;
  });
  document.querySelectorAll("#kanban .col").forEach(col => {
    col.ondragover = e => { e.preventDefault(); col.classList.add("over"); };
    col.ondragleave = () => col.classList.remove("over");
    col.ondrop = async e => {
      e.preventDefault(); col.classList.remove("over");
      const tid = e.dataTransfer.getData("text/plain");
      try { await api("PATCH", `/api/projects/${S.cur}/tasks/${tid}`, { column: col.dataset.col }); } catch (err) { toast(err.message); }
    };
  });
  document.querySelectorAll("#kanban [data-del]").forEach(b => b.onclick = async e => {
    e.stopPropagation(); if (!confirm(`Remove task ${b.dataset.del} from the board?`)) return;
    await api("DELETE", `/api/projects/${S.cur}/tasks/${b.dataset.del}`);
  });
}
function patchCards(id) {
  if (!S.proj) return;
  document.querySelectorAll(`#kanban .col[data-col="In Progress"] .card[data-who="${CSS.escape(id)}"]`).forEach(el => {
    const t = S.proj.tasks.find(x => x.id === el.dataset.tid); if (t) el.outerHTML = cardHtml(t);
  });
  document.querySelectorAll(`#kanban .card[data-who="${CSS.escape(id)}"]`).forEach(card => {
    card.ondragstart = e => { e.dataTransfer.setData("text/plain", card.dataset.tid); card.style.opacity = .4; };
    card.ondragend = () => card.style.opacity = 1;
  });
}
function renderTaskWho() {
  const proj = S.proj && S.proj.project;
  $("taskWho").innerHTML = `<option value="">unassigned</option>` +
    ((proj && proj.team) || []).map(id => agentById(id)).filter(Boolean).map(a => `<option value="${esc(a.id)}">${esc(a.avatar)} ${esc(a.name)}</option>`).join("");
}
$("addTask").onsubmit = async e => {
  e.preventDefault(); const title = $("taskTitle").value.trim(); if (!title || !S.cur) return;
  await api("POST", `/api/projects/${S.cur}/tasks`, { title, assignee: $("taskWho").value || null });
  $("taskTitle").value = "";
};

// ---------------------------------------------------------------- changelog
const seenEntries = new Set();
function entryHtml(c) {
  const key = `${S.cur}|${c.t}|${c.who}|${c.text.slice(0, 40)}`, fresh = !seenEntries.has(key); seenEntries.add(key);
  return entryInner(c, fresh);
}
function entryInner(c, fresh) {
  const a = agentById(c.who);
  const face = a ? `<span class="av">${avatarSVG(a, false)}</span>` : `<span class="av" style="display:grid;place-items:center">${c.who === "you" ? "🧑" : "•"}</span>`;
  return `<div class="entry ${fresh ? "fresh" : ""}">${face}<div><div class="meta"><b>${esc(a ? a.name : c.who)}</b> · ${esc((c.t || "").slice(11, 16))}</div>${esc(c.text)}</div></div>`;
}
function renderFeed() {   // activity timeline, grouped by day, newest first
  const list = ((S.proj && S.proj.changelog) || []).slice().reverse();
  let day = null, html = "";
  for (const c of list) {
    const d = (c.t || "").slice(0, 10);
    if (d !== day) { day = d; html += `<div class="day">${esc(d)}</div>`; }
    html += entryHtml(c);
  }
  $("changelog").innerHTML = html || `<span class="hint">Nothing yet. Run a sprint and watch the team work.</span>`;
}

// ---------------------------------------------------------------- drawer (agent inspector)
async function openDrawer(id) {
  S.drawer = id;
  let data; try { data = await api("GET", `/api/agents/${id}`); } catch (e) { return toast(e.message); }
  const a = data.agent, i = S.agents.findIndex(x => x.id === id); if (i >= 0) S.agents[i] = { ...S.agents[i], ...a };
  const on = awake(a), isP = a.kind === "principal";
  const proj = S.projects.find(p => p.slug === a.project);
  const cur = S.projects.find(p => p.slug === S.cur);
  const d = $("drawer");
  d.style.setProperty("--c", a.color);
  d.innerHTML = `
    <div class="dr-h"><div class="av">${avatarSVG(a, on)}</div>
      <div><h3>${esc(a.name)} ${esc(a.avatar)}</h3><div class="hint">${esc(a.role)}</div>
        <div class="pmeta">${isP ? `<span class="chip">oversees all projects</span>` : a.project ? `<span class="chip lock">🔒 ${esc(proj ? proj.name : a.project)}</span>` : `<span class="chip">🪑 on the Sideline</span>`}
          <select id="drModel" class="chip model" title="Model this agent uses">${["sonnet", "opus", "haiku"].map(m => `<option ${m === a.model ? "selected" : ""}>${m}</option>`).join("")}</select></div></div>
      <button class="btn sm close" id="drClose">✕</button></div>
    <div class="dr-b">
      <div id="drLive"></div>
      <div class="kv"><span>Tone</span><span>${esc(a.tone)}</span>${a.skills.length ? `<span>Skills</span><span>${esc(a.skills.join(", "))}</span>` : ""}
        <span>Created</span><span>${esc(a.created || "")} ${a.created_by ? `by ${esc(a.created_by)}` : ""}</span>
        <span>Usage</span><span>${a.live.calls || 0} turns this session · ≈ ${money(a.live.cost_usd)}</span>
        <span>Profile</span><span><code>.agents/${esc(a.id)}.md</code></span></div>
      ${isP ? "" : `<div class="webbox"><div class="row"><b>🌐 Web access</b>
          <span class="chip ${S.settings.allow_web ? (a.web.length ? "ok" : "") : "off"}">${!a.web.length ? "none" : S.settings.allow_web ? `${a.web.length} site${a.web.length > 1 ? "s" : ""} allowed` : "web is switched off in Settings"}</span></div>
        <textarea id="drWeb" rows="2" placeholder="Sites this agent may fetch, e.g. arxiv.org, doi.org, lol.fandom.com (empty = no web)">${esc(a.web.join(", "))}</textarea>
        <div class="dr-actions"><button class="btn sm" id="drWebSave">Save sites</button>
          <span class="hint">Only these domains: Claude Code blocks everything else. Only you can change this.</span></div></div>`}
      <div class="bio">${md(a.bio)}</div>
      <div class="dr-actions">
        ${isP ? "" : a.project ? `<button class="btn" id="drRelease">🪑 Move to the Sideline</button>`
          : cur ? `<button class="btn go" id="drAssign">＋ Draft into “${esc(cur.name)}”</button>` : ""}
        <button class="btn" id="drStop" ${on ? "" : "disabled"}>■ Stop turn</button>
        ${isP ? "" : `<button class="btn danger" id="drDelete" style="margin-left:auto">🗑 Delete agent</button>`}
      </div>
      ${isP ? `<div class="ask"><b>Talk to ${esc(a.name)}</b><span class="hint">${esc(a.name)} keeps a conversation per project and can change the board and team for you.</span>
          <div class="dr-actions"><button class="btn primary" id="drChat">💬 Open the chat${cur ? ` for “${esc(cur.name)}”` : ""}</button></div></div>`
      : `<div class="ask"><b>Prompt ${esc(a.name)} directly</b>
        <textarea id="drAsk" placeholder="${a.project ? `Works inside “${esc(proj ? proj.name : a.project)}” with its shared memory…` : "On the Sideline: a chat without project access…"}"></textarea>
        <div class="dr-actions"><button class="btn primary" id="drSend">Send</button><span class="hint">Runs one \`claude -p\` turn on your subscription.</span></div></div>`}
      <div><b>Terminal output</b><div class="term" id="drTerm"></div></div>
    </div>`;
  $("drTerm").innerHTML = data.log.map(logLine).join("");
  $("drTerm").scrollTop = 1e9;
  renderDrawerLive();
  $("drClose").onclick = closeDrawer;
  $("drModel").onchange = async e => { await api("PATCH", `/api/agents/${id}`, { model: e.target.value }); toast(`${a.name} now uses ${e.target.value}`); };
  if ($("drWebSave")) $("drWebSave").onclick = async () => {
    try {
      const r = await api("PATCH", `/api/agents/${id}`, { web: $("drWeb").value });
      toast(r.agent.web.length ? `${a.name} may fetch: ${r.agent.web.join(", ")}` : `${a.name} has no web access`);
      openDrawer(id);
    } catch (e) { toast(e.message); }
  };
  if ($("drRelease")) $("drRelease").onclick = async () => { try { await api("POST", `/api/agents/${id}/release`); toast(`${a.name} is resting on the Sideline`); openDrawer(id); } catch (e) { toast(e.message); } };
  if ($("drAssign")) $("drAssign").onclick = async () => { try { await api("POST", `/api/agents/${id}/assign`, { slug: S.cur }); toast(`${a.name} joined the team`); openDrawer(id); } catch (e) { toast(e.message); } };
  $("drStop").onclick = () => api("POST", `/api/agents/${id}/stop`);
  if ($("drDelete")) $("drDelete").onclick = async () => {
    if (!confirm(`Delete ${a.name} (${a.role})?\n\nThe profile is moved to .agents/.trash, and they leave their project. Only you can do this.`)) return;
    try { await api("DELETE", `/api/agents/${id}`, { confirm: id }); toast(`${a.name} was deleted`); closeDrawer(); } catch (e) { toast(e.message); }
  };
  if ($("drChat")) $("drChat").onclick = () => { closeDrawer(); setView("chat"); };
  if ($("drSend")) $("drSend").onclick = async () => {
    const text = $("drAsk").value.trim(); if (!text) return;
    try { await api("POST", `/api/agents/${id}/prompt`, { text }); $("drAsk").value = ""; toast(`${a.name} is on it`); } catch (e) { toast(e.message); }
  };
  $("scrim").hidden = false; requestAnimationFrame(() => d.classList.add("open"));
}
function renderDrawerLive() {
  const a = agentById(S.drawer), el = $("drLive"); if (!a || !el) return;
  if (!awake(a)) { el.innerHTML = ""; }
  else {
    if (!el.querySelector(".dr-live")) el.innerHTML = `<div class="dr-live" style="--c:${esc(a.color)}"></div>`;
    setBubble(el.querySelector(".dr-live"), a);
  }
  const av = document.querySelector("#drawer .dr-h .av"); if (av) av.innerHTML = avatarSVG(a, awake(a));
  const stop = $("drStop"); if (stop) stop.disabled = !awake(a);
}
function closeDrawer() { S.drawer = null; $("drawer").classList.remove("open"); $("scrim").hidden = true; }
$("scrim").onclick = closeDrawer;
document.addEventListener("keydown", e => { if (e.key === "Escape") { if (!$("modal").hidden && !S.busyModal) closeModal(); else closeDrawer(); } });
function logLine(e) { return `<div class="k-${esc(e.kind)}"><span class="ts">${esc(e.t)}</span>${esc(e.text)}</div>`; }

// ---------------------------------------------------------------- brief -> staffing proposal -> assemble
function closeModal() { $("modal").hidden = true; $("modal").innerHTML = ""; }
// ---------------------------------------------------------------- file uploads: streamed (no size limit), with progress
const FILE_ICON = { text: "📄", image: "🖼️", pdf: "📕", data: "📊", file: "📦" };
const fmtSize = n => n >= 1073741824 ? (n / 1073741824).toFixed(2) + " GB" : n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";
function uploadRaw(url, file, onProgress) {   // the file itself is the request body, so nothing is held in memory
  return new Promise((ok, bad) => {
    const x = new XMLHttpRequest();
    x.open("POST", url);
    x.setRequestHeader("Content-Type", "application/octet-stream");
    x.upload.onprogress = e => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    x.onload = () => { let d = {}; try { d = JSON.parse(x.responseText); } catch (e) { /* empty */ }
      x.status < 300 ? ok(d) : bad(new Error(d.detail || `Upload failed (${x.status})`)); };
    x.onerror = () => bad(new Error("Upload failed: the studio server didn't respond."));
    x.send(file);
  });
}
// the upload tray (bottom-right): one row per file with a live progress bar
const TRAY = [];
function renderTray() {
  let t = $("tray");
  if (!t) { document.body.insertAdjacentHTML("beforeend", `<div class="tray" id="tray"></div>`); t = $("tray"); }
  t.hidden = !TRAY.length;
  t.innerHTML = TRAY.map(u => `<div class="trow ${u.state}"><span>${FILE_ICON[u.kind] || "📦"}</span><div class="tmain"><div class="tname"><b>${esc(u.name)}</b>
      <i>${u.state === "error" ? esc(u.error) : u.state === "done" ? "added to inputs/" : `${Math.round(u.p * 100)}% of ${fmtSize(u.size)}`}</i></div>
      <div class="tbar"><i style="width:${Math.round(u.p * 100)}%"></i></div></div></div>`).join("");
}
function kindGuess(name) {
  const ext = (name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
  if ([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"].includes(ext)) return "image";
  if (ext === ".pdf") return "pdf";
  if ([".csv", ".tsv", ".json", ".jsonl", ".parquet", ".xlsx", ".xls", ".sqlite", ".db", ".zip", ".gz", ".feather", ".h5", ".npy", ".pkl"].includes(ext)) return "data";
  return "file";
}
async function addFilesToProject(list, slug = S.cur) {
  const proj = S.projects.find(p => p.slug === slug); if (!proj) return toast("Pick a project first.");
  for (const f of [...list]) {
    const u = { name: f.name, size: f.size, kind: kindGuess(f.name), p: 0, state: "up" }; TRAY.push(u); renderTray();
    try {
      await uploadRaw(`/api/projects/${encodeURIComponent(slug)}/files/raw?name=${encodeURIComponent(f.name)}`, f, p => { u.p = p; renderTray(); });
      u.p = 1; u.state = "done";
    } catch (e) { u.state = "error"; u.error = e.message; }
    renderTray();
    setTimeout(() => { const i = TRAY.indexOf(u); if (i >= 0 && u.state === "done") { TRAY.splice(i, 1); renderTray(); } }, 4000);
  }
  if (TRAY.some(u => u.state === "done")) toast(`Added to “${proj.name}” · the team sees them from their next turn`);
}
// drop files anywhere in the window (outside the brief) to add them to the current project, even while it's running
(function globalDrop() {
  document.body.insertAdjacentHTML("beforeend", `<div class="dropall" id="dropAll" hidden><div><span>📎</span><b id="dropAllTitle"></b><i>Files go to the project's inputs/ folder · any size</i></div></div>`);
  let depth = 0;
  const has = e => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
  const modalOpen = () => !$("modal").hidden;
  window.addEventListener("dragenter", e => { if (!has(e) || modalOpen() || !S.cur) return; depth++;
    const p = S.projects.find(x => x.slug === S.cur); $("dropAllTitle").textContent = `Drop to add to “${p ? p.name : ""}”`; $("dropAll").hidden = false; });
  window.addEventListener("dragleave", e => { if (!has(e) || modalOpen()) return; depth = Math.max(0, depth - 1); if (!depth) $("dropAll").hidden = true; });
  window.addEventListener("dragover", e => { if (has(e) && !modalOpen() && S.cur) e.preventDefault(); });
  window.addEventListener("drop", e => {
    if (!has(e) || modalOpen()) return;
    e.preventDefault(); depth = 0; $("dropAll").hidden = true;
    if (S.cur && e.dataTransfer.files.length) addFilesToProject(e.dataTransfer.files);
  });
})();

// attachments for the brief being written: { token, files: [{name, size, kind}], pending: [{name, size, p}] }
S.brief = { token: null, files: [], pending: [] };
function renderAttachments() {
  const el = $("bFileList"); if (!el) return;
  el.innerHTML = S.brief.files.map(f => `<span class="fchip">${FILE_ICON[f.kind] || "📦"} <b>${esc(f.name)}</b><i>${fmtSize(f.size)}</i>
      <button type="button" title="Remove" data-rm="${esc(f.name)}">✕</button></span>`).join("")
    + S.brief.pending.map(u => `<span class="fchip busy"><span class="spin"></span><b>${esc(u.name)}</b><i>${Math.round(u.p * 100)}% of ${fmtSize(u.size)}</i>
      <span class="fbar"><i style="width:${Math.round(u.p * 100)}%"></i></span></span>`).join("");
  el.querySelectorAll("[data-rm]").forEach(b => b.onclick = async () => {
    try { const r = await api("DELETE", `/api/uploads/${S.brief.token}/${encodeURIComponent(b.dataset.rm)}`); S.brief.files = r.files; renderAttachments(); }
    catch (e) { toast(e.message); }
  });
  const dz = $("bDrop"); if (dz) dz.classList.toggle("has", S.brief.files.length > 0 || S.brief.pending.length > 0);
}
async function addBriefFiles(list) {
  for (const f of [...list]) {   // one at a time; each streams straight to disk
    const u = { name: f.name, size: f.size, p: 0 }; S.brief.pending.push(u); renderAttachments();
    try {
      const r = await uploadRaw(`/api/uploads/raw?name=${encodeURIComponent(f.name)}&token=${S.brief.token || ""}`, f, p => { u.p = p; renderAttachments(); });
      S.brief.token = r.token; S.brief.files = r.files;
    } catch (e) { toast(`${f.name}: ${e.message}`); }
    S.brief.pending.splice(S.brief.pending.indexOf(u), 1); renderAttachments();
  }
}
function openBrief(slug, keep) {
  if (!keep) S.brief = { token: null, files: [], pending: [] };
  const proj = slug ? S.projects.find(p => p.slug === slug) : null;
  $("modal").hidden = false;
  $("modal").innerHTML = `<form class="sheet" id="briefForm">
    <h1>${proj ? `Re-staff “${esc(proj.name)}”` : "New project brief"}</h1>
    <div class="sub">${proj ? "Tell the Principal what changed or what extra help you need." : "Describe what you want to build, your goals and any constraints. The Principal will propose a team before anyone starts."}</div>
    <label class="field"><span>Project name</span><input id="bName" value="${esc(proj ? proj.name : "")}" ${proj ? "readonly" : ""} maxlength="80" placeholder="e.g. Recipe planner web app" required></label>
    <label class="field"><span>Brief</span><textarea id="bBrief" placeholder="What should it do? Who is it for? Must-haves, nice-to-haves, tech preferences, deadlines, things to avoid…" required></textarea></label>
    <div class="field"><span>Files (optional)</span>
      <div class="dropzone" id="bDrop" tabindex="0" role="button" aria-label="Attach files">
        <input type="file" id="bFiles" multiple hidden>
        <div class="dz-msg"><span class="dz-ic">📎</span><div><b>Drop files here</b> or <u>browse</u><br>
          <span class="hint">specs, raw data, designs, existing code, PDFs, images · any size · saved to the project's <code>inputs/</code> folder for every agent</span></div></div>
        <div class="dz-files" id="bFileList"></div>
      </div></div>
    <div class="sheet-f"><span class="hint">Available now: ${S.agents.filter(a => a.kind !== "principal" && (!a.project || a.project === slug)).length} agents · locked elsewhere: ${S.agents.filter(a => a.project && a.project !== slug).length}</span>
      <button type="button" class="btn" onclick="closeModal()">Cancel</button><button class="btn primary" id="bGo">Ask ${esc((principalAgent() || {}).name || "the Principal")} for a team →</button></div></form>`;
  $("bBrief").focus();
  renderAttachments();
  const dz = $("bDrop"), sheet = $("briefForm");
  dz.onclick = e => { if (!e.target.closest("[data-rm]")) $("bFiles").click(); };
  dz.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("bFiles").click(); } };
  $("bFiles").onchange = e => { addBriefFiles(e.target.files); e.target.value = ""; };
  // the whole brief window accepts dropped files; the drop zone lights up while you drag
  sheet.ondragover = e => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); dz.classList.add("over"); } };
  sheet.ondragleave = e => { if (!sheet.contains(e.relatedTarget)) dz.classList.remove("over"); };
  sheet.ondrop = e => { if (!e.dataTransfer.files.length) return; e.preventDefault(); dz.classList.remove("over"); addBriefFiles(e.dataTransfer.files); };
  $("briefForm").onsubmit = async e => {
    e.preventDefault();
    if (S.brief.pending.length) return toast("Wait for the files to finish uploading.");
    const name = $("bName").value.trim(), brief = $("bBrief").value.trim();
    showThinking(name);
    try { const prop = await api("POST", "/api/projects/propose", { name, brief, slug: slug || null, upload: S.brief.token }); S.busyModal = false; showProposal(name, brief, prop, slug); }
    catch (err) { S.busyModal = false; toast(err.message); openBrief(slug, true); $("bName").value = name; $("bBrief").value = brief; }
  };
}
function showThinking(name) {
  const p = principalAgent() || { name: "Principal", color: "#fbbf24", avatar: "🧭" };
  S.busyModal = true;
  $("modal").innerHTML = `<div class="sheet"><div class="thinking">
    <div class="pdesk-av working" style="--c:${esc(p.color)}">${avatarSVG(p, true)}<div class="bubble">${bubbleHtml(p)}</div></div>
    <h1 style="margin-top:60px">${esc(p.name)} is reviewing the roster</h1>
    <div class="sub">Reading your brief for “${esc(name)}”, checking who is free, spotting parallel work and skill gaps…</div></div></div>`;
}
function showProposal(name, brief, prop, slug) {
  const draftCards = prop.draft.map((d, i) => {
    const a = agentById(d.id); if (!a) return "";
    return `<label class="pick on" style="--c:${esc(a.color)}"><input type="checkbox" data-draft="${esc(a.id)}" checked>
      <span class="av">${avatarSVG(a, true)}</span><span><b>${esc(a.name)}</b> <span class="role">· ${esc(a.role)}</span>
      <div class="why">${esc(d.reason || "")}</div><div class="hint" style="margin-top:4px">${esc(a.bio.replace(/\*\*/g, "").slice(0, 120))}…</div></span></label>`;
  }).join("");
  const recruitCards = prop.recruit.map((r, i) => {
    const a = { name: r.name, avatar: r.avatar || "🤖", color: /^#[0-9a-f]{6}$/i.test(r.color || "") ? r.color : "#5ee6c4" };
    return `<label class="pick on" style="--c:${esc(a.color)}"><input type="checkbox" data-recruit="${i}" checked>
      <span class="av">${avatarSVG(a, true)}</span><span><span class="new">NEW RECRUIT</span><br><b>${esc(r.name)}</b> <span class="role">· ${esc(r.role || "")} · ${esc(r.model || "sonnet")}</span>
      <div class="why">${esc(r.why || "")}</div><div class="hint" style="margin-top:4px">${esc(r.personality || "")}</div></span></label>`;
  }).join("");
  const tasks = prop.tasks.map((t, i) => `<label><input type="checkbox" data-task="${i}" checked> ${esc(t.title)} <span class="hint">→ ${esc(t.assignee || "unassigned")}</span></label>`).join("");
  $("modal").innerHTML = `<div class="sheet">
    <h1>Team staffing plan</h1><div class="sub">for “${esc(name)}” · proposed by ${esc((principalAgent() || {}).name)} · ≈ ${money(prop.cost_usd)} of usage</div>
    <div class="plan-sum">${esc(prop.summary || "")}</div>
    ${S.brief.files.length ? `<div class="attached">📎 ${S.brief.files.length} file${S.brief.files.length > 1 ? "s" : ""} go to <code>inputs/</code>: ${S.brief.files.map(f => esc(f.name)).join(", ")}</div>` : ""}
    <div class="sec-h"><h2>Draft existing agents</h2><span class="hint">${prop.draft.length ? "untick anyone you don't want" : "none of the available agents fit"}</span></div>
    <div class="picks">${draftCards || `<span class="hint">—</span>`}</div>
    <div class="sec-h"><h2>Recruit new specialists</h2><span class="hint">${prop.recruit.length ? "approved recruits get a profile in .agents/" : "no gaps found"}</span></div>
    <div class="picks">${recruitCards || `<span class="hint">—</span>`}</div>
    <div class="sec-h"><h2>First tasks</h2><span class="hint">go to “Up Next” on the board</span></div>
    <div class="tasklist">${tasks || `<span class="hint">—</span>`}</div>
    <div class="sheet-f"><span class="hint">Nothing runs until you press “Run sprint”.</span>
      <button class="btn" id="pBack">← Edit brief</button><button class="btn primary" id="pGo">✓ Approve &amp; Assemble Team</button></div></div>`;
  $("modal").querySelectorAll(".pick input").forEach(i => i.onchange = () => i.closest(".pick").classList.toggle("on", i.checked));
  $("pBack").onclick = () => { openBrief(slug, true); $("bName").value = name; $("bBrief").value = brief; };
  $("pGo").onclick = async () => {
    const draft = [...$("modal").querySelectorAll("[data-draft]:checked")].map(i => i.dataset.draft);
    const recruit = [...$("modal").querySelectorAll("[data-recruit]:checked")].map(i => prop.recruit[+i.dataset.recruit]);
    const taskList = [...$("modal").querySelectorAll("[data-task]:checked")].map(i => prop.tasks[+i.dataset.task]);
    $("pGo").disabled = true; $("pGo").textContent = "Assembling…";
    try {
      const r = await api("POST", "/api/projects", { name, brief, summary: prop.summary, draft, recruit, tasks: taskList, slug: slug || null, upload: S.brief.token });
      S.brief = { token: null, files: [], pending: [] };
      closeModal(); toast(`Team assembled for “${r.project.name}”`);
      S.cur = r.project.slug; try { localStorage.setItem("studio.project", S.cur); } catch (e) { /* ignore */ }
      await load();
    } catch (e) { toast(e.message); $("pGo").disabled = false; $("pGo").textContent = "✓ Approve & Assemble Team"; }
  };
}
$("newProjBtn").onclick = () => openBrief();
$("settingsBtn").onclick = () => {
  const s = S.settings;
  $("modal").hidden = false;
  $("modal").innerHTML = `<form class="sheet" id="setForm" style="max-width:560px">
    <h1>Studio settings</h1><div class="sub">Stored in state/settings.json.</div>
    <label class="field"><span>Agents working in parallel (max)</span><input type="number" id="sPar" min="1" max="8" value="${s.max_parallel}"></label>
    <label class="field"><span>Autopilot: maximum sprint cycles per run</span><input type="number" id="sCyc" min="1" max="20" value="${s.autopilot_cycles}"></label>
    <label class="tasklist"><label><input type="checkbox" id="sBash" ${s.allow_bash ? "checked" : ""}>
      Let agents run terminal commands (Bash), e.g. to run tests or process data. Commands start in the project folder but aren't locked to it.</label>
      <label><input type="checkbox" id="sWeb" ${s.allow_web ? "checked" : ""}>
      Allow web (master switch). Each agent may then fetch pages <b>only</b> from the sites in its own web list (set in its profile drawer); Claude Code blocks every other site. Web search stays off.</label></label>
    <div class="sheet-f"><span class="hint">Every turn runs on your Claude subscription via the CLI.</span>
      <button type="button" class="btn" onclick="closeModal()">Cancel</button><button class="btn primary">Save</button></div></form>`;
  $("setForm").onsubmit = async e => {
    e.preventDefault();
    S.settings = await api("PUT", "/api/settings", { max_parallel: +$("sPar").value, autopilot_cycles: +$("sCyc").value, allow_bash: $("sBash").checked, allow_web: $("sWeb").checked });
    closeModal(); toast("Settings saved"); renderPrincipal();
  };
};
window.openBrief = openBrief; window.closeModal = closeModal;

// ---------------------------------------------------------------- live events
let refreshT = null;
function softRefresh() { clearTimeout(refreshT); refreshT = setTimeout(load, 150); }
let boardT = null;
function boardRefresh() {
  clearTimeout(boardT);
  boardT = setTimeout(async () => {
    if (!S.cur) return;
    try { S.proj = await api("GET", `/api/projects/${S.cur}`); } catch (e) { return; }
    const i = S.projects.findIndex(p => p.slug === S.cur); if (i >= 0) S.projects[i] = { ...S.projects[i], ...S.proj.project };
    renderBoard(); renderFeed(); renderMemory(); renderPrincipal(); renderTabs(); renderTaskWho();
  }, 120);
}
function connect() {
  const es = new EventSource("/api/events");
  es.onmessage = m => {
    const ev = JSON.parse(m.data);
    if (ev.type === "agent") {
      const a = agentById(ev.id); if (!a) return softRefresh();
      a.live = ev.live; patchAgent(ev.id);
    } else if (ev.type === "log") {
      if (S.drawer === ev.id && $("drTerm")) {
        const t = $("drTerm"), atEnd = t.scrollHeight - t.scrollTop - t.clientHeight < 40;
        t.insertAdjacentHTML("beforeend", logLine(ev.entry)); if (atEnd) t.scrollTop = 1e9;
      }
    } else if (ev.type === "board" || ev.type === "changelog") {
      if (ev.slug === S.cur && ev.type === "changelog" && S.view !== "activity") S.unread++;
      if (ev.slug === S.cur) boardRefresh();
    } else if (ev.type === "sprint") {
      const p = S.projects.find(x => x.slug === ev.slug); if (p) p.sprint = ev.sprint;
      if (S.proj && S.proj.project.slug === ev.slug) S.proj.project.sprint = ev.sprint;
      renderTabs(); if (ev.slug === S.cur) renderPrincipal();
    } else if (ev.type === "roster") softRefresh();
    else if (ev.type === "files") { if (ev.slug === S.cur) renderFiles(); }
    else if (ev.type === "chat" || ev.type === "chat_update" || ev.type === "chat_wait") onChatEvent(ev);
  };
  es.onerror = () => { $("cliBadge").classList.add("bad"); setTimeout(() => api("GET", "/api/health").then(h => { S.cli = h.cli; renderCli(); }).catch(() => {}), 2500); };
  es.onopen = () => renderCli();
}

load().then(connect).catch(e => { document.body.insertAdjacentHTML("beforeend", `<div class="toast show">Couldn't reach the studio server: ${esc(e.message)}</div>`); });
