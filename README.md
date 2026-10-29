# KippieSearch

Lokale, privacy-first **semantic search + RAG-chat**. Draait als **Home Assistant add-on** op een Raspberry Pi 5 (8 GB, aarch64, HA OS): één container, één proces (FastAPI + chat-UI + REST + MCP + scheduler). Bestanden blijven op je LAN. Embeddings en het chatmodel draaien op de Pi, niet in de cloud.

Poort **8765** (configureerbaar; vrij ten opzichte van HA 8123, Radarr 7878, Sonarr 8989, qBittorrent 8082, Jellyfin 8096, Portainer 9000, Fladder 8098). Ingress in Home Assistant zit erbij.

## Architectuur

```
Browser / Grok Bot
        │  HTTP 8765
        ▼
┌──────────────────────────────────────────┐
│  FastAPI (één proces)                    │
│  /           chat-UI (zoeken + RAG)      │
│  /api/*      search, chat SSE, status    │
│  /mcp        Streamable HTTP MCP         │
│  APScheduler dagelijkse schoolsync + NAS │
└──────────────┬───────────────────────────┘
               │
     ┌─────────┴──────────┐
     │ LanceDB (/data)    │  embedded, geen extra service
     │ EmbeddingGemma 2   │  ONNX q4, text-only, offline na 1e start
     │ Gemma 3 GGUF       │  llama-cpp-python, lazy load, unload na idle
     └─────────┬──────────┘
               │
     nas_roots (expliciet, bv. /share/<naam>) ── NAS (SMB)
     SMB-fallback (smbprotocol, read-only)
     /data/itslearning  ← dagelijkse sync (requests, geen Chromium)
```

### Keuzes

| Onderdeel | Keuze | Waarom |
|-----------|--------|--------|
| Vector DB | **LanceDB** (embedded) | Geen extra proces, Arrow-native, licht op ARM. Qdrant embedded/Chroma trekken meer runtime. |
| Embeddings | **EmbeddingGemma 2** ONNX **q4** (`onnx/model_q4.onnx`) | Text-encoder ~174 MB. De graph heeft nog `image_features`/`video_features`/`audio_features`; we voeren 0-rij tensors in (zelfde truc als FastEmbed). Vision/audio-encoders worden niet gedownload. Fallback: `hash`. |
| Prompts | `task: search result \| query: …` / `title: … \| text: …` | Officiële query/document-prompts van EmbeddingGemma 2. |
| Dimensie | **256** (MRL, her-genormaliseerd) | Bijna volle tekstkwaliteit, 3× minder vectoropslag. 128/512/768 instelbaar. |
| Base image | `python:3.12-slim-bookworm` | Geen Alpine: onnxruntime heeft glibc-wheels voor aarch64. |
| it's learning | `requests` + sessie-login, daarna REST-cookie of HTML-crawl | Officiële REST API eist OAuth-client van school. Playwright/Chromium is te zwaar naast HA op 8 GB. |
| Auth | Bearer-token voor API/MCP; **ingress zonder token** | LAN + Tailscale beveiligen met token; HA-gebruiker is al ingelogd. |
| Chat-LLM | **llama-cpp-python** in-process, CPU-wheels (aarch64/amd64) | Geen extra serverproces. Lazy load + unload na idle spaart RAM naast HA. Default: Gemma 3 1B instruct Q4_K_M. |
| Chat-UI | Vanilla HTML/CSS/JS, relatieve paden | Geen build-stap. Werkt achter HA-ingress. Donker/licht, Pixel-layout. |

### Mapstructuur

```
repository.yaml                 HA add-on repository
kippiesearch/                   de add-on (build-context)
  config.yaml                   Supervisor-config, opties, ingress
  Dockerfile
  run.sh
  requirements.txt
  translations/{en,nl}.yaml
  src/kippiesearch/             Python-pakket
    main.py                     FastAPI
    embeddings.py / indexer.py / store.py / search.py / chat.py
    itslearning.py / nas.py / mcp_server.py
    static/                     chat-UI HTML/CSS/JS (geen build-stap)
tests/  fixtures/  docker-compose.yml  .github/workflows/ci.yml
```

Persistent op de Pi (`/data` van de add-on): `lancedb/`, `models/` (EmbeddingGemma + GGUF, éénmalige download), `itslearning/`, `nas-cache/`.

## Installatie op Home Assistant OS

HA OS heeft geen vrije Docker Compose en geen systemd-units voor extra stacks. Daarom is dit een **custom add-on**. Deze GitHub-repo is public; Supervisor kan hem dus als add-on-repository clonen.

### Primair — custom repository (aanbevolen)

1. HA → Instellingen → Add-ons → Add-on Store → ⋮ (rechtsboven) → **Repositories**.
2. Voeg toe: `https://github.com/KippenBoutske/kippiesearch`
3. Ververs de store. **KippieSearch** verschijnt in de lijst.
4. Installeren (eerste build op de Pi duurt even: Python-deps + later het model).
5. **Configuratie:** zet een `api_token`, koppel de NAS (zie hieronder) en eventueel it's learning, daarna **Start**.
6. Sidebar **KippieSearch** (ingress) of `http://<pi-ip>:8765/`.

De repository-root bevat `repository.yaml`; de add-on zelf staat in `kippiesearch/` (`config.yaml` + `Dockerfile`). Dat is het formaat dat de Supervisor verwacht.

### Alternatief — lokale add-on via `/addons`

Handig voor ontwikkeling of als de store de repo niet pakt:

1. Installeer de **Samba**- of **SSH**-add-on in HA.
2. Kopieer de map `kippiesearch/` uit deze repo **als** `/addons/kippiesearch/` (daarin moeten `config.yaml` en `Dockerfile` staan).
3. HA → Instellingen → Add-ons → ⋮ → **Check for updates** / ververs.
4. Onder **Local add-ons** verschijnt KippieSearch. Verder hetzelfde als hierboven.

### Eerste start

- Health: `http://<pi-ip>:8765/health`
- Eerste keer met `embedder: onnx` downloadt EmbeddingGemma 2 q4 naar `/data/models` (honderden MB, eenmalig, daarna offline).
- Daarna **Status → Index opnieuw**.

## NAS-koppeling

Wachtwoorden horen alleen in de HA-opties, nooit in git. Host, share en inlog zijn **leeg tot je ze invult**; zonder die waarden blijft SMB uit (zichtbaar in status/logs).

### Voorkeur — HA-netwerkopslag (add-on mount `/share`)

Een add-on kan op HA OS niet zelf SMB-mounten. Laat Home Assistant dat doen:

1. HA → Instellingen → Systeem → **Opslag** → **netwerkopslag toevoegen**.
2. Server: het LAN-adres van je NAS, bijvoorbeeld `192.168.1.x`
3. Protocol: **Share** (SMB/CIFS)
4. Share: de sharenaam op de NAS, bijvoorbeeld `home` of `documents`
5. Gebruikersnaam + wachtwoord van dat NAS-account (in HA, niet in deze repo).
6. Kies een naam, bijvoorbeeld `nas`. HA mount de share dan onder `/share/<naam>` (dus `/share/nas` als je hem `nas` noemt).
7. In KippieSearch: zet `nas_roots` op **dat pad**, bijvoorbeeld `/share/nas`. Laat het **niet** op heel `/share` staan: daar zet HA app-data van andere add-ons (caches, plugins, JS-bundles). Default is leeg tot je een pad invult.

Alleen lezen is genoeg.

### Alternatief — SMB-fallback in de add-on

Als er geen HA-mount is: `nas_smb_enabled: true` en vul `nas_smb_host` (bijv. `192.168.1.x`), `nas_smb_shares` (bijv. `home`), `nas_smb_username` en `nas_smb_password` in. Poort default 445. De add-on leest via `smbprotocol` (read-only) en cachet naar `/data/nas-cache`. Ontbreekt host, share of gebruiker, dan wordt SMB overgeslagen.

## Credentials veilig zetten

- Zet **nooit** wachtwoorden in git of in de UI-zoekbalk.
- HA-opties van het type `password` (API-token, SMB, it's learning) staan in `/data/options.json` van de add-on, niet in de repo.
- Logs redacteren `password` / `token` / `authorization`.
- Kies een lang `api_token`. Stuur het alleen als `Authorization: Bearer …`.
- Ingress (sidebar) heeft **geen** token nodig.

## Index opnieuw bouwen

- UI → Status → **Index opnieuw** (incrementeel: mtime+size, daarna content-hash) of **Volledige herindex**.
- `POST /api/reindex` met `{"full": false, "itslearning": false}`
- `POST /api/reindex` met `{"itslearning": true}` triggert eerst schoolsync.

Incrementeel slaat ongewijzigde bestanden over, herindexeert bij hash-wijziging, en gooit verwijderde paden uit de index.

Ondersteunde formaten: txt/md/html/json, code-extensies, PDF (pypdf), DOCX, PPTX. Afbeeldingen: overgeslagen (EmbeddingGemma text-only; OCR-optie bestaat maar staat uit vanwege RAM).

## MCP toevoegen aan Grok Bot

Endpoint: Streamable HTTP op hetzelfde proces.

- URL: `http://<pi-ip>:8765/mcp` (LAN of Tailscale)
- Auth: `Authorization: Bearer <api_token>`
- Tools: `search(query, top_k, source?, file_type?)`, `ask(question, top_k, source?, file_type?)` (RAG-antwoord + bronnen), `list_sources()`, `get_file_content(path, max_chars)` (alleen tekst, alleen geïndexeerde roots, path-traversal geblokkeerd)

In Grok / Cursor: custom MCP-server, transport Streamable HTTP, URL hierboven, header Bearer. HA-ingress is niet nodig voor MCP; Grok praat rechtstreeks naar poort 8765.

## it's learning

- Zet `itslearning_enabled: true` en vul URL, gebruikersnaam en wachtwoord in (geen SSO).
- URL-voorbeeld: `https://<school>.itslearning.com` — er is geen default-school in de repo.
- Organisatienaam is optioneel (alleen ter herkenning in de UI).
- Dagelijks (cron `0 3 * * *`, Europe/Amsterdam): login → cursusbestanden naar `/data/itslearning` → index.
- Rate-limit standaard 0,75 s. Alleen wat jíj mag zien.
- Zonder URL of credentials blijft sync uit; status en logs zeggen wat er ontbreekt.

Playwright zit er bewust niet in (Chromium-RAM op de Pi).

## Lokaal testen (gewone machine)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
KIPPIESEARCH_EMBEDDER=hash pytest -q
KIPPIESEARCH_DATA_DIR=./data KIPPIESEARCH_NAS_ROOTS=./fixtures/corpus \
  KIPPIESEARCH_EMBEDDER=hash KIPPIESEARCH_AUTH_DISABLED=true \
  python -m kippiesearch
```

Of: `docker compose up --build` → http://127.0.0.1:8765 (hash-embedder, fixtures als `/share`).

API: `GET /api/search?q=…`, `POST /api/chat` (SSE), `/api/sources`, `/api/status`, `POST /api/reindex`, `/health`.

## Pi-tuning

| Optie | Default | Tip op Pi 5 + HA |
|--------|---------|------------------|
| `batch_size` | 4 | lager bij RAM-druk |
| `onnx_threads` | 2 | laat cores over voor HA |
| `max_file_mb` | 32 | skip ISO/video via excludes |
| `throttle_s` | 0.05 | verhoog als HA traag wordt |
| `max_seq_length` | 512 | 256 spaart RAM, 1024 is zwaarder |
| `embedding_dim` | 256 | 128 nog kleiner, 768 voller |

## RAG-chat

Flow: vraag → EmbeddingGemma-retrieval (top-k chunks) → prompt met bronnen → gestreamd antwoord, met `[1]` / `[2]` die naar bronkaarten wijzen. Staat het niet in de bestanden, dan zegt het model dat eerlijk.

- `POST /api/chat` — SSE (`sources`, `token`, `done`, `error`)
- MCP-tool `ask` — zelfde flow, zonder stream
- Model-download bij **eerste chat**, daarna offline in `/data/models`
- Lazy load; na `chat_idle_unload_minutes` (default 15) gaat het GGUF uit RAM

| Optie | Default | Betekenis |
|--------|---------|-----------|
| `chat_enabled` | true | Zet uit als je alleen wilt zoeken |
| `chat_model` | `gemma3-1b` | Preset `gemma3-1b` / `gemma3-4b`, of `org/repo:file.gguf`, of `mock` |
| `chat_n_ctx` | 2048 | Klein houden op de Pi |
| `chat_n_threads` | 2 | Cores overlaten aan HA |
| `chat_max_tokens` | 512 | Max. antwoordlengte |
| `chat_temperature` | 0.3 | Lager = zakelijker |
| `chat_idle_unload_minutes` | 15 | `0` = in RAM houden |
| `chat_top_k` | 6 | Chunks in de RAG-prompt |

Runtime: **llama-cpp-python** in hetzelfde proces (CPU-wheels via de extra-index van het project, aarch64 + amd64). Lichter dan een aparte llama.cpp-server: geen tweede proces, unload is `del` + `gc`.

## RAM / snelheid

Gemeten op de ontwikkel-VM (x86_64, hash-embedder + mock-LLM, kleine testdocs via `python scripts/benchmark.py` en pytest):

- index/search: honderden ms / enkele ms (hash, geen ONNX)
- mock-chat SSE: tokens meteen, geen GGUF-RAM

Realistische Pi 5 (8 GB, naast Home Assistant):

| Onderdeel | Schijf | Extra RAM (geladen) | tok/s (verwacht, CPU) |
|-----------|--------|---------------------|------------------------|
| EmbeddingGemma 2 q4 | ~174 MB | ~450–900 MB proces met index | n.v.t. (query tientallen–honderden ms) |
| Gemma 3 1B instruct Q4_K_M | ~806 MB | **~1,2–1,8 GB** extra | **~15–30 tok/s** |
| Gemma 3 4B instruct Q4_K_M | ~2,5 GB | **~3,2–4,0 GB** extra | **~5–12 tok/s** |

1B is de default omdat 4B naast HA + *arr + Jellyfin krap wordt op 8 GB. Idle-unload (15 min) geeft het geheugen terug. Zet `chat_enabled: false` als je het model helemaal niet wilt.

Houd headroom voor Home Assistant. Begin met de defaults (`gemma3-1b`, 2 threads, ctx 2048).

## Troubleshooting

| Symptoom | Check |
|----------|--------|
| Add-on bouwt niet | Logs van de Supervisor-build; aarch64 + netwerk naar PyPI/GHCR. |
| Model-download faalt | Eerste start heeft uitgaand HTTPS naar Hugging Face nodig; daarna offline. |
| Geen NAS-hits | `nas_roots` leeg? Zet het pad van je netwerkopslag (`/share/<naam>`), niet heel `/share`. Of SMB aan met host, share, user en wachtwoord? |
| Zoeken: image_features missing | V0.1.2 bug: de q4-ONNX-graph eist lege multimodal-tensors. Vanaf 0.2.0 worden die als 0-rij gevoed. Herstart na update; model opnieuw downloaden is niet nodig. |
| Index: `'_text'` / KeyError | V0.1.2 bug: een mislukte embed-batch verpestte volgende bestanden. Vanaf 0.2.0 blijft metadata intact en wordt de fout gelogd met type + traceback. |
| it's learning-fout | URL, user/wachtwoord, geen SSO; kijk `/api/status` last_error (geen secrets in logs). |
| 401 op API/MCP | `Authorization: Bearer …`; ingress slaat token over. |
| HA wordt traag | `onnx_threads=1`, grotere `throttle_s`, kleinere `batch_size`. |
| MCP bereikbaar? | Vanaf je laptop/Tailscale: `curl -H "Authorization: Bearer …" http://<pi>:8765/health` |

## Licentie

MIT. EmbeddingGemma 2: Apache 2.0 (Google).
