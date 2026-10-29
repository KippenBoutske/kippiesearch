const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const KEY = "kippiesearch.chats.v1";
const THEME_KEY = "kippiesearch.theme";
const state = {
  mode: "chat",
  chats: [],
  activeId: null,
  filters: { source: "", file_type: "" },
  streaming: false,
};

function loadStore() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "{}");
    state.chats = Array.isArray(raw.chats) ? raw.chats : [];
    state.activeId = raw.activeId || (state.chats[0] && state.chats[0].id) || null;
  } catch {
    state.chats = [];
  }
}
function saveStore() {
  localStorage.setItem(KEY, JSON.stringify({ chats: state.chats, activeId: state.activeId }));
}
function uid() {
  return crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
}
function activeChat() {
  return state.chats.find((c) => c.id === state.activeId) || null;
}
function ensureChat() {
  let chat = activeChat();
  if (chat) return chat;
  chat = { id: uid(), title: "Nieuw gesprek", updatedAt: Date.now(), messages: [] };
  state.chats.unshift(chat);
  state.activeId = chat.id;
  saveStore();
  return chat;
}

function tokenHeaders() {
  const token = localStorage.getItem("kippiesearch_token") || "";
  const headers = { "Content-Type": "application/json", Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { ...tokenHeaders(), ...(opts.headers || {}) } });
  if (res.status === 401) {
    const token = prompt("API-token (Bearer) voor KippieSearch:");
    if (token) {
      localStorage.setItem("kippiesearch_token", token);
      return api(path, opts);
    }
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || res.statusText);
  }
  return res.json();
}

function setTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem(THEME_KEY, theme);
  $("#theme-toggle").textContent = theme === "dark" ? "Licht thema" : "Donker thema";
}
function toggleTheme() {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  setTheme(next);
}

function setMode(mode) {
  state.mode = mode;
  $$(".modes button").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  $$(".stage").forEach((el) => el.classList.toggle("hidden", el.dataset.view !== mode));
  const titles = {
    chat: ["Chat", "Vraag iets over je bestanden"],
    search: ["Zoeken", "Semantische treffers, zonder LLM"],
    status: ["Status", "Index, RAM en chatmodel"],
  };
  $("#view-title").textContent = titles[mode][0];
  $("#view-sub").textContent = titles[mode][1];
  $("#q").placeholder = mode === "search" ? "Zoek in je bestanden…" : "Vraag iets over je bestanden…";
  $("#composer").classList.toggle("hidden", mode === "status");
  $("#filters").classList.toggle("hidden", mode === "status");
  if (mode === "status") loadStatus();
  if (mode === "search" && !$("#results").innerHTML) renderSearchEmpty();
  closeSidebar();
}

function openSidebar() {
  $("#sidebar").classList.add("open");
  $("#scrim").hidden = false;
}
function closeSidebar() {
  $("#sidebar").classList.remove("open");
  $("#scrim").hidden = true;
}

function renderHistory() {
  const box = $("#history");
  if (!state.chats.length) {
    box.innerHTML = `<p class="tiny muted" style="padding:8px">Nog geen gesprekken.</p>`;
    return;
  }
  box.innerHTML = state.chats
    .map(
      (c) => `<div class="hist-item ${c.id === state.activeId ? "active" : ""}" data-id="${c.id}">
        <span>${escapeHtml(c.title || "Gesprek")}</span>
        <button type="button" class="del" data-del="${c.id}" aria-label="Verwijder">✕</button>
      </div>`
    )
    .join("");
}

function renderThread() {
  const chat = activeChat();
  const thread = $("#thread");
  if (!chat || !chat.messages.length) {
    thread.innerHTML = `<div class="empty">
      <h2>Wat wil je weten?</h2>
      <p class="muted">Antwoorden komen uit jouw NAS- en schoolbestanden. Niets verlaat dit apparaat.</p>
      <div class="examples">
        <button type="button" data-q="Vat de belangrijkste punten uit mijn notities samen">Samenvatten</button>
        <button type="button" data-q="Waar staat het recept voor pasta?">Recept zoeken</button>
        <button type="button" data-q="Welke opdrachten gaan over Mars?">Opdracht vinden</button>
      </div>
    </div>`;
    return;
  }
  thread.innerHTML = chat.messages.map(renderMessage).join("");
  thread.scrollTop = thread.scrollHeight;
}

function renderMessage(msg) {
  if (msg.role === "user") {
    return `<article class="msg user"><div class="bubble">${escapeHtml(msg.content)}</div></article>`;
  }
  const body = formatAssistant(msg.content || "");
  const typing = msg.pending && !msg.content ? `<div class="typing" aria-hidden="true"><i></i><i></i><i></i></div>` : "";
  return `<article class="msg assistant" id="${msg.domId || ""}">
    <div class="bubble">${body || typing}</div>
    ${renderSources(msg.sources || [])}
  </article>`;
}

function renderSources(sources) {
  if (!sources.length) return "";
  const cards = sources
    .map((src) => {
      const open = src.open || `api/file?path=${encodeURIComponent(src.path || "")}`;
      const dl = src.download || `${open}${open.includes("?") ? "&" : "?"}download=1`;
      const score = src.score != null ? Number(src.score).toFixed(2) : "—";
      return `<article class="card source-card" id="src-${src.n}">
        <header>
          <h4 title="${escapeHtml(src.path || "")}">${escapeHtml(src.filename || src.title || "bestand")}</h4>
          <span class="badge">${escapeHtml(src.source || "?")}</span>
        </header>
        <div class="snippet">${escapeHtml(src.snippet || "")}</div>
        <div class="meta"><span>score ${score}</span><span>${escapeHtml(src.file_type || "")}</span></div>
        <div class="actions">
          <a href="${open}" target="_blank" rel="noopener">Openen</a>
          <a href="${dl}">Download</a>
        </div>
      </article>`;
    })
    .join("");
  return `<div class="sources"><h3>Bronnen</h3><div class="source-grid">${cards}</div></div>`;
}

function formatAssistant(text) {
  const escaped = escapeHtml(text);
  return escaped.replace(/\[(\d+)\]/g, '<a class="cite" href="#src-$1">$1</a>').replace(/\n/g, "<br>");
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderSearchEmpty() {
  $("#results").innerHTML = `<div class="empty">
    <h2>Semantisch zoeken</h2>
    <p class="muted">Alleen treffers, geen chatmodel. Handig als je het bestand zelf wilt openen.</p>
  </div>`;
}

function renderHits(hits) {
  const box = $("#results");
  if (!hits.length) {
    box.innerHTML = `<p class="muted">Geen treffers. Probeer andere woorden of indexeer eerst.</p>`;
    return;
  }
  box.innerHTML = hits
    .map((hit) => {
      const open = `api/file?path=${encodeURIComponent(hit.path)}`;
      const name = (hit.title || hit.path || "").split("/").pop();
      return `<article class="card">
        <header class="source-card" style="padding:0;box-shadow:none;background:transparent;border:0">
          <header>
            <h4>${escapeHtml(name)}</h4>
            <span class="badge">${escapeHtml(hit.source || "?")}</span>
          </header>
        </header>
        <div class="meta">
          <span>${escapeHtml(hit.file_type || "")}</span>
          <span>score ${hit.score}</span>
        </div>
        <div class="snippet">${hit.snippet || ""}</div>
        <p class="path">${escapeHtml(hit.path)}</p>
        <div class="actions">
          <a href="${open}" target="_blank" rel="noopener">Openen</a>
          <a href="${open}&download=1">Download</a>
        </div>
      </article>`;
    })
    .join("");
}

function filterParams() {
  const params = new URLSearchParams();
  if (state.filters.source) params.set("source", state.filters.source);
  if (state.filters.file_type) params.set("file_type", state.filters.file_type);
  const after = $("#after").value;
  const before = $("#before").value;
  if (after) params.set("after", after);
  if (before) params.set("before", before);
  return params;
}

async function doSearch(q) {
  const params = filterParams();
  params.set("q", q);
  params.set("top_k", "10");
  $("#results").innerHTML = `<p class="muted">Zoeken…</p>`;
  try {
    const data = await api(`api/search?${params.toString()}`);
    renderHits(data.results || []);
  } catch (err) {
    $("#results").innerHTML = `<p class="err">${escapeHtml(err.message)}</p>`;
  }
}

async function parseSse(res, onEvent) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop();
    for (const block of parts) {
      let event = "message";
      const dataLines = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;
      try {
        onEvent(event, JSON.parse(dataLines.join("\n")));
      } catch {
        onEvent(event, { raw: dataLines.join("\n") });
      }
    }
  }
}

async function doChat(q) {
  const chat = ensureChat();
  if (chat.title === "Nieuw gesprek") chat.title = q.slice(0, 42);
  chat.messages.push({ role: "user", content: q });
  const assistant = { role: "assistant", content: "", sources: [], pending: true, domId: "msg-" + uid() };
  chat.messages.push(assistant);
  chat.updatedAt = Date.now();
  saveStore();
  renderHistory();
  renderThread();
  state.streaming = true;
  $("#send").disabled = true;
  const payload = {
    message: q,
    top_k: 6,
    source: state.filters.source || null,
    file_type: state.filters.file_type || null,
    after: $("#after").value || null,
    before: $("#before").value || null,
  };
  try {
    const res = await fetch("api/chat", {
      method: "POST",
      headers: tokenHeaders(),
      body: JSON.stringify(payload),
    });
    if (res.status === 401) {
      const token = prompt("API-token (Bearer) voor KippieSearch:");
      if (token) localStorage.setItem("kippiesearch_token", token);
      throw new Error("Niet ingelogd");
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(body.detail || res.statusText);
    }
    await parseSse(res, (event, data) => {
      if (event === "sources") assistant.sources = data.sources || [];
      if (event === "token") assistant.content += data.text || "";
      if (event === "done") assistant.content = data.answer || assistant.content;
      if (event === "error") assistant.content = data.error || "Er ging iets mis.";
      assistant.pending = event !== "done" && event !== "error";
      const el = document.getElementById(assistant.domId);
      if (el) el.outerHTML = renderMessage(assistant);
      else renderThread();
      $("#thread").scrollTop = $("#thread").scrollHeight;
    });
  } catch (err) {
    assistant.content = err.message;
    assistant.pending = false;
    renderThread();
  } finally {
    assistant.pending = false;
    saveStore();
    state.streaming = false;
    $("#send").disabled = false;
    $("#q").focus();
  }
}

async function loadStatus() {
  const el = $("#status-view");
  el.innerHTML = `<p class="muted">Status laden…</p>`;
  try {
    const data = await api("api/status");
    const store = data.store || {};
    const ram = data.ram || {};
    const chat = data.chat || {};
    const per = store.per_source || {};
    const sources =
      Object.entries(per)
        .map(
          ([name, s]) =>
            `<article class="card"><p class="muted">${escapeHtml(name)}</p><p class="stat">${s.files}</p><p>${s.chunks} chunks</p></article>`
        )
        .join("") || `<article class="card"><p>Nog niets geïndexeerd.</p></article>`;
    const notices = (data.notices || []).map((n) => `<li>${escapeHtml(n)}</li>`).join("") || "<li>geen</li>";
    const errors = (data.errors || []).map((e) => `<li><code>${escapeHtml(e.path)}</code> — ${escapeHtml(e.error)}</li>`).join("") || "<li>geen</li>";
    el.innerHTML = `
      <div class="grid">
        <article class="card"><p class="muted">Bestanden</p><p class="stat">${store.files || 0}</p><p>${store.chunks || 0} chunks</p></article>
        <article class="card"><p class="muted">Fase</p><p class="stat">${escapeHtml(data.phase || "idle")}</p><p>${escapeHtml(data.message || "")}</p></article>
        <article class="card"><p class="muted">RAM (RSS)</p><p class="stat">${ram.rss_mb ?? "?"} MB</p><p>vrij ${ram.system_available_mb ?? "?"} MB</p></article>
        <article class="card"><p class="muted">Chat</p><p class="stat">${chat.enabled ? (chat.loaded ? "geladen" : "klaar") : "uit"}</p><p>${escapeHtml(chat.label || chat.model || "")}</p></article>
      </div>
      <h2>Per bron</h2>
      <div class="grid">${sources}</div>
      <article class="card">
        <p>Model: ${escapeHtml(chat.model || "—")} · backend ${escapeHtml(chat.backend || "—")}</p>
        <p>Context ${chat.n_ctx || "—"} · threads ${chat.n_threads || "—"} · unload na ${chat.idle_unload_minutes ?? "—"} min</p>
        <p>Verwacht op Pi 5: 1B ≈ 1,2–1,8 GB extra RAM, 4B ≈ 3,2–4,0 GB. 1B grofweg 15–30 tok/s, 4B 5–12 tok/s.</p>
        <p>Laatste index: ${escapeHtml(store.last_index || "—")}</p>
        <p>Laatste NAS-scan: ${escapeHtml(store.last_nas_scan || "—")}</p>
        <p>Laatste schoolsync: ${escapeHtml(store.last_itslearning_sync || "—")}</p>
        <h3>Bronnen</h3>
        <ul>${notices}</ul>
        <div class="actions" style="margin-top:12px">
          <button class="primary" id="reindex">Index opnieuw</button>
          <button class="ghost" id="reindex-full" style="width:auto">Volledige herindex</button>
          <button class="ghost" id="sync-school" style="width:auto">Sync school + index</button>
        </div>
      </article>
      <article class="card"><h3>Fouten</h3><ul>${errors}</ul></article>
    `;
    $("#reindex").onclick = () => reindex(false, false);
    $("#reindex-full").onclick = () => reindex(true, false);
    $("#sync-school").onclick = () => reindex(false, true);
    $("#model-pill").textContent = chat.enabled ? chat.model || "chat" : "chat uit";
  } catch (err) {
    el.innerHTML = `<p class="err">${escapeHtml(err.message)}</p>`;
  }
}

async function reindex(full, itslearning) {
  try {
    await api("api/reindex", { method: "POST", body: JSON.stringify({ full, itslearning }) });
    loadStatus();
  } catch (err) {
    alert(err.message);
  }
}

function submitQuery(text) {
  const q = (text || $("#q").value).trim();
  if (!q || state.streaming) return;
  $("#q").value = "";
  autosize();
  if (state.mode === "search") doSearch(q);
  else {
    setMode("chat");
    doChat(q);
  }
}

function autosize() {
  const ta = $("#q");
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
}

function newChat() {
  const chat = { id: uid(), title: "Nieuw gesprek", updatedAt: Date.now(), messages: [] };
  state.chats.unshift(chat);
  state.activeId = chat.id;
  saveStore();
  renderHistory();
  renderThread();
  setMode("chat");
  $("#q").focus();
}

$("#composer").addEventListener("submit", (ev) => {
  ev.preventDefault();
  submitQuery();
});
$("#q").addEventListener("input", autosize);
$("#q").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    submitQuery();
  }
});
$$(".modes button").forEach((btn) => btn.addEventListener("click", () => setMode(btn.dataset.mode)));
$$(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    const key = chip.dataset.filter;
    state.filters[key] = chip.dataset.value;
    $$(`.chip[data-filter="${key}"]`).forEach((c) => c.classList.toggle("active", c === chip));
  });
});
$("#history").addEventListener("click", (ev) => {
  const del = ev.target.closest("[data-del]");
  if (del) {
    state.chats = state.chats.filter((c) => c.id !== del.dataset.del);
    if (state.activeId === del.dataset.del) state.activeId = state.chats[0] ? state.chats[0].id : null;
    saveStore();
    renderHistory();
    renderThread();
    return;
  }
  const item = ev.target.closest("[data-id]");
  if (!item) return;
  state.activeId = item.dataset.id;
  saveStore();
  renderHistory();
  renderThread();
  setMode("chat");
});
$("#thread").addEventListener("click", (ev) => {
  const ex = ev.target.closest("[data-q]");
  if (ex) submitQuery(ex.dataset.q);
});
$("#new-chat").addEventListener("click", newChat);
$("#theme-toggle").addEventListener("click", toggleTheme);
$("#open-sidebar").addEventListener("click", openSidebar);
$("#close-sidebar").addEventListener("click", closeSidebar);
$("#scrim").addEventListener("click", closeSidebar);

loadStore();
setTheme(document.documentElement.getAttribute("data-theme") || "dark");
renderHistory();
renderThread();
setMode("chat");
api("api/status")
  .then((data) => {
    const chat = data.chat || {};
    $("#model-pill").textContent = chat.enabled ? chat.model || "lokaal" : "chat uit";
  })
  .catch(() => {});
