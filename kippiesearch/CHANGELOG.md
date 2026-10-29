# Changelog

## 0.2.0

- Chat-UI (ChatGPT/Perplexity-stijl): donker/licht, bubbels, streaming, bronkaarten, zijbalk-historie, filterchips, aparte zoekmodus, status in dezelfde stijl
- RAG-chat met lokaal GGUF-model via llama-cpp-python (in-process, lazy load, unload na idle)
- Presets: Gemma 3 1B instruct Q4_K_M (default) en Gemma 3 4B; download naar /data bij eerste gebruik
- `POST /api/chat` (SSE) en MCP-tool `ask`
- Relatieve paden voor Home Assistant ingress; Pixel-vriendelijke layout
- Fix: EmbeddingGemma 2 q4-ONNX voedt lege `image_features`/`video_features`/`audio_features` (text-only, zelfde bestand)
- Fix: index-batch verloor `_text` na een embed-fout; logs hebben nu exception-type + traceback
- Default `nas_roots` is leeg (niet heel `/share`); plugins/cache/minified JS worden altijd overgeslagen

## 0.1.2

- Geen persoonlijke defaults meer: SMB-host, shares, it's learning-URL en schoolnaam zijn leeg tot je ze invult
- Ontbrekende host/URL/credentials schakelen die bron uit (status + logs)

## 0.1.1

- SMB-share-opties en HA-netwerkopslag-documentatie
- Publieke add-on-repository als primaire installatie

## 0.1.0

- Eerste release: FastAPI UI + API + MCP in één proces
- Incrementele indexing met LanceDB en EmbeddingGemma 2 (ONNX q4)
- HA add-on (aarch64/amd64), ingress, configureerbare poort 8765
- it's learning-sync via requests (geen Chromium)
- SMB-fallback voor Synology
