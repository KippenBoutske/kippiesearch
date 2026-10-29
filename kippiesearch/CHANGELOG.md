# Changelog

## 0.1.1

- Default SMB-share is Synology `home` op `192.168.1.x` (user/wachtwoord blijven leeg)
- README: publieke add-on-repository als primaire installatie; HA-netwerkopslag-route voor share `home`

## 0.1.0

- Eerste release: FastAPI UI + API + MCP in één proces
- Incrementele indexing met LanceDB en EmbeddingGemma 2 (ONNX q4)
- HA add-on (aarch64/amd64), ingress, configureerbare poort 8765
- it's learning-sync via requests (geen Chromium)
- SMB-fallback voor Synology
