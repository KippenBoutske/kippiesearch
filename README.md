# KippieSearch

Lokale, privacy-first **semantic search**. Draait als **Home Assistant add-on** op een Raspberry Pi 5 (8 GB, aarch64, HA OS): één container, één proces (FastAPI + web-UI + REST + MCP + scheduler). Bestanden blijven op je LAN. Embeddings gebeuren op de Pi, niet in de cloud.

Poort **8765** (configureerbaar; vrij ten opzichte van HA 8123, Radarr 7878, Sonarr 8989, qBittorrent 8082, Jellyfin 8096, Portainer 9000, Fladder 8098). Ingress in Home Assistant zit erbij.

## Architectuur

```
Browser / Grok Bot
        │  HTTP 8765
        ▼
┌──────────────────────────────────────────┐
│  FastAPI (één proces)                    │
│  /           web-UI                      │
│  /api/*      search, status, reindex     │
│  /mcp        Streamable HTTP MCP         │
│  APScheduler dagelijkse schoolsync + NAS │
└──────────────┬───────────────────────────┘
               │
     ┌─────────┴──────────┐
     │ LanceDB (/data)    │  embedded, geen extra service
     │ EmbeddingGemma 2   │  ONNX q4, text-only, offline na 1e start
     └─────────┬──────────┘
               │
     /share & /media (HA netwerkopslag) ── Synology 192.168.1.x
     SMB-fallback (smbprotocol, read-only)
     /data/itslearning  ← dagelijkse sync (requests, geen Chromium)
```

### Keuzes

| Onderdeel | Keuze | Waarom |
|-----------|--------|--------|
| Vector DB | **LanceDB** (embedded) | Geen extra proces, Arrow-native, licht op ARM. Qdrant embedded/Chroma trekken meer runtime. |
| Embeddings | **EmbeddingGemma 2** text-only, ONNX **q4** (`onnx-community/embeddinggemma-2-ONNX`) | Nieuwere, lichtere text-backbone (270M i.p.v. 300M), beter op code. q4 ≈ 174 MB. Vision/audio uit (RAM). Fallback: `hash`-embedder (tests) of sentence-transformers extra. |
| Prompts | `task: search result \| query: …` / `title: … \| text: …` | Officiële query/document-prompts van EmbeddingGemma 2. |
| Dimensie | **256** (MRL, her-genormaliseerd) | Bijna volle tekstkwaliteit, 3× minder vectoropslag. 128/512/768 instelbaar. |
| Base image | `python:3.12-slim-bookworm` | Geen Alpine: onnxruntime heeft glibc-wheels voor aarch64. |
| it's learning | `requests` + sessie-login, daarna REST-cookie of HTML-crawl | Officiële REST API eist OAuth-client van school. Playwright/Chromium is te zwaar naast HA op 8 GB. |
| Auth | Bearer-token voor API/MCP; **ingress zonder token** | LAN + Tailscale beveiligen met token; HA-gebruiker is al ingelogd. |

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
    embeddings.py / indexer.py / store.py / search.py
    itslearning.py / nas.py / mcp_server.py
    static/                     HTML/CSS/JS (geen build-stap)
tests/  fixtures/  docker-compose.yml  .github/workflows/ci.yml
```

Persistent op de Pi (`/data` van de add-on): `lancedb/`, `models/` (EmbeddingGemma, éénmalige download), `itslearning/`, `nas-cache/`.

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

## NAS-koppeling (Synology `home` op `192.168.1.x`)

Doel: de persoonlijke Synology-**home**-share indexeren. Die share mappt naar de homemap van de SMB-gebruiker waarmee je inlogt. Wachtwoorden horen alleen in de HA-opties, nooit in git.

### Voorkeur — HA-netwerkopslag (add-on mount `/share`)

Een add-on kan op HA OS niet zelf SMB-mounten. Laat Home Assistant dat doen:

1. HA → Instellingen → Systeem → **Opslag** → **netwerkopslag toevoegen**.
2. Server: `192.168.1.x`
3. Protocol: **Share** (SMB/CIFS)
4. Share: `home`
5. Gebruikersnaam + wachtwoord van dat Synology-account (in HA, niet in deze repo).
6. Kies een naam, bijvoorbeeld `home`. HA mount de share dan onder `/share/<naam>` (dus `/share/home` als je hem `home` noemt).
7. In KippieSearch: zet `nas_roots` op dat pad, bijvoorbeeld `/share/home`. Standaard staan `/share` en `/media` aan; `/share` indexeert alles wat HA daar neerzet, `/share/home` beperkt het tot die ene mount.

Alleen lezen is genoeg.

### Alternatief — SMB-fallback in de add-on

Als er geen HA-mount is: `nas_smb_enabled: true`, vul `nas_smb_username` en `nas_smb_password` in. Defaults: host `192.168.1.x`, share `home`, poort 445. De add-on leest via `smbprotocol` (read-only) en cachet naar `/data/nas-cache`. Zonder gebruikersnaam gebeurt er niets.

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
- Tools: `search(query, top_k, source?, file_type?)`, `list_sources()`, `get_file_content(path, max_chars)` (alleen tekst, alleen geïndexeerde roots, path-traversal geblokkeerd)

In Grok / Cursor: custom MCP-server, transport Streamable HTTP, URL hierboven, header Bearer. HA-ingress is niet nodig voor MCP; Grok praat rechtstreeks naar poort 8765.

## it's learning (je school, je regio)

- Login met gebruikersnaam + wachtwoord (geen SSO).
- URL en schoolnaam zijn configureerbaar (default `https://school.itslearning.com`).
- Dagelijks (cron `0 3 * * *`, Europe/Amsterdam): login → cursusbestanden naar `/data/itslearning` → index.
- Rate-limit standaard 0,75 s. Alleen wat jíj mag zien.
- **Handmatig nodig:** echte login testen. Zonder credentials blijft sync uit.

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

API: `GET /api/search?q=…`, `/api/sources`, `/api/status`, `POST /api/reindex`, `/health`.

## Pi-tuning

| Optie | Default | Tip op Pi 5 + HA |
|--------|---------|------------------|
| `batch_size` | 4 | lager bij RAM-druk |
| `onnx_threads` | 2 | laat cores over voor HA |
| `max_file_mb` | 32 | skip ISO/video via excludes |
| `throttle_s` | 0.05 | verhoog als HA traag wordt |
| `max_seq_length` | 512 | 256 spaart RAM, 1024 is zwaarder |
| `embedding_dim` | 256 | 128 nog kleiner, 768 voller |

## RAM / snelheid

Gemeten op de ontwikkel-VM (x86_64, 4 vCPU, hash-embedder, 44 kleine docs via `python scripts/benchmark.py`):

- index: **0,52 s** (44 bestanden)
- query: **6,6 ms**
- RSS na index/search: **~197 MB** (proces laadt ook onnxruntime-libs mee; draaiende FastAPI ~235 MB)

Realistische Pi 5 (8 GB, naast HA + *arr + Jellyfin):

- Zonder ONNX-model (hash): grofweg 80–150 MB RSS.
- Met EmbeddingGemma 2 q4: model ~174 MB op schijf; runtime grofweg **450–900 MB** RSS, piek hoger tijdens eerste download.
- Indexeren: CPU-bound; tientallen tekstbestanden/minuut is haalbaar met throttle. Grote PDF’s langzamer.
- Query na warmup: tientallen tot een paar honderd ms.

Houd headroom voor Home Assistant. Begin met de defaults.

## Troubleshooting

| Symptoom | Check |
|----------|--------|
| Add-on bouwt niet | Logs van de Supervisor-build; aarch64 + netwerk naar PyPI/GHCR. |
| Model-download faalt | Eerste start heeft uitgaand HTTPS naar Hugging Face nodig; daarna offline. |
| Geen NAS-hits | HA-netwerkopslag `home` gemount? `nas_roots` = `/share/<naam>`? Of SMB aan, share `home`, user+wachtwoord ingevuld? |
| it's learning-fout | URL, user/wachtwoord, geen SSO; kijk `/api/status` last_error (geen secrets in logs). |
| 401 op API/MCP | `Authorization: Bearer …`; ingress slaat token over. |
| HA wordt traag | `onnx_threads=1`, grotere `throttle_s`, kleinere `batch_size`. |
| MCP bereikbaar? | Vanaf je laptop/Tailscale: `curl -H "Authorization: Bearer …" http://<pi>:8765/health` |

## Licentie

MIT. EmbeddingGemma 2: Apache 2.0 (Google).
