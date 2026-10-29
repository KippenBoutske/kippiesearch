const $ = (sel) => document.querySelector(sel);
const resultsEl = $("#results");
const statusEl = $("#status-view");
const searchView = $("#search-view");

function tokenHeaders() {
  const token = localStorage.getItem("kippiesearch_token") || "";
  const headers = { "Content-Type": "application/json" };
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

function show(view) {
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  searchView.classList.toggle("hidden", view !== "search");
  statusEl.classList.toggle("hidden", view !== "status");
  if (view === "status") loadStatus();
}

function fmtDate(ts) {
  if (!ts) return "—";
  const n = Number(ts);
  const d = Number.isFinite(n) && n > 100000 ? new Date(n * 1000) : new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  return d.toLocaleString("nl-NL");
}

function renderHits(hits) {
  if (!hits.length) {
    resultsEl.innerHTML = `<p class="empty">Geen treffers. Probeer andere woorden of indexeer eerst.</p>`;
    return;
  }
  resultsEl.innerHTML = hits
    .map((hit) => {
      const open = `/api/file?path=${encodeURIComponent(hit.path)}`;
      const dl = `${open}&download=1`;
      return `<article class="card">
        <h3>${hit.title || hit.path.split("/").pop()}</h3>
        <div class="meta">
          <span class="pill">${hit.source || "?"}</span>
          <span class="pill">${hit.file_type || ""}</span>
          <span class="pill">score ${hit.score}</span>
          <span class="pill">${fmtDate(hit.mtime)}</span>
        </div>
        <div class="snippet">${hit.snippet || ""}</div>
        <p class="path">${hit.path}</p>
        <div class="actions">
          <a href="${open}" target="_blank" rel="noopener">Openen</a>
          <a href="${dl}">Download</a>
        </div>
      </article>`;
    })
    .join("");
}

async function doSearch(ev) {
  ev?.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  const params = new URLSearchParams({ q, top_k: $("#top_k").value || "8" });
  const source = $("#source").value;
  const fileType = $("#file_type").value;
  const after = $("#after").value;
  const before = $("#before").value;
  if (source) params.set("source", source);
  if (fileType) params.set("file_type", fileType);
  if (after) params.set("after", after);
  if (before) params.set("before", before);
  resultsEl.innerHTML = `<p class="hint">Zoeken…</p>`;
  try {
    const data = await api(`/api/search?${params.toString()}`);
    renderHits(data.results || []);
  } catch (err) {
    resultsEl.innerHTML = `<p class="err">${err.message}</p>`;
  }
}

async function loadStatus() {
  statusEl.innerHTML = `<p class="hint">Status laden…</p>`;
  try {
    const data = await api("/api/status");
    const store = data.store || {};
    const per = store.per_source || {};
    const ram = data.ram || {};
    const sources = Object.entries(per)
      .map(([name, s]) => `<article class="card"><p class="tag">${name}</p><p class="stat">${s.files} bestanden</p><p>${s.chunks} chunks</p></article>`)
      .join("") || `<article class="card"><p>Nog niets geïndexeerd.</p></article>`;
    const errors = (data.errors || [])
      .map((e) => `<li><code>${e.path}</code> — ${e.error}</li>`)
      .join("") || "<li>geen</li>";
    statusEl.innerHTML = `
      <div class="grid">
        <article class="card"><p class="tag">Totaal</p><p class="stat">${store.files || 0}</p><p>${store.chunks || 0} chunks</p></article>
        <article class="card"><p class="tag">Fase</p><p class="stat">${data.phase}</p><p>${data.message || ""}</p></article>
        <article class="card"><p class="tag">RAM (RSS)</p><p class="stat">${ram.rss_mb ?? "?"} MB</p><p>systeem vrij ${ram.system_available_mb ?? "?"} MB</p></article>
        <article class="card"><p class="tag">Embedder</p><p>${(data.embedder && data.embedder.backend) || "?"}</p><p>${(data.embedder && data.embedder.model) || ""} · dim ${(data.embedder && data.embedder.dim) || ""}</p></article>
      </div>
      <h2>Per bron</h2>
      <div class="grid">${sources}</div>
      <article class="card">
        <p>Laatste index: ${store.last_index || "—"}</p>
        <p>Laatste NAS-scan: ${store.last_nas_scan || "—"}</p>
        <p>Laatste it's learning-sync: ${store.last_itslearning_sync || "—"}</p>
        <p>Huidig bestand: ${data.current_file || "—"} (${data.processed || 0}/${data.total || 0})</p>
        <p>Laatste fout: ${store.last_error || "geen"}</p>
        <div class="actions">
          <button class="primary" id="reindex">Index opnieuw</button>
          <button class="ghost" id="reindex-full">Volledige herindex</button>
          <button class="ghost" id="sync-school">Sync it's learning + index</button>
        </div>
      </article>
      <article class="card"><h3>Fouten</h3><ul>${errors}</ul></article>
    `;
    $("#reindex").onclick = () => reindex(false, false);
    $("#reindex-full").onclick = () => reindex(true, false);
    $("#sync-school").onclick = () => reindex(false, true);
  } catch (err) {
    statusEl.innerHTML = `<p class="err">${err.message}</p>`;
  }
}

async function reindex(full, itslearning) {
  try {
    await api("/api/reindex", { method: "POST", body: JSON.stringify({ full, itslearning }) });
    loadStatus();
  } catch (err) {
    alert(err.message);
  }
}

$("#search-form").addEventListener("submit", doSearch);
document.querySelectorAll("nav button").forEach((btn) => btn.addEventListener("click", () => show(btn.dataset.view)));
show("search");
