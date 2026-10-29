from fastapi.testclient import TestClient

from kippiesearch.chat import ChatEngine, MockLLM, build_user_prompt, resolve_chat_spec
from kippiesearch.main import create_app
from kippiesearch.search import SearchService


def test_presets_resolve_without_personal_data():
    one = resolve_chat_spec("gemma3-1b")
    four = resolve_chat_spec("gemma3-4b")
    custom = resolve_chat_spec("org/model:weights.gguf")
    assert one["filename"].endswith(".gguf")
    assert four["repo_id"].startswith("unsloth/")
    assert custom["filename"] == "weights.gguf"
    dumped = str(one) + str(four)
    assert "192.168" not in dumped


def test_prompt_admits_missing_sources():
    prompt = build_user_prompt("Waar is dit?", [])
    assert "geen bronfragmenten" in prompt.lower()


def test_mock_llm_streams_tokens():
    llm = MockLLM()
    pieces = list(llm.stream([{"role": "user", "content": "Er zijn geen bronfragmenten gevonden.\n\nVraag: xyz"}]))
    text = "".join(pieces)
    assert "niet in de geïndexeerde bestanden" in text
    assert pieces


def test_chat_ask_with_index(indexed_app):
    settings, store, embedder, _, _ = indexed_app
    engine = ChatEngine(settings, SearchService(store, embedder))
    result = engine.ask("Wat is Mars?")
    assert result["answer"]
    assert "[1]" in result["answer"] or result["sources"]
    assert result["sources"]


def test_chat_sse_api(indexed_app):
    settings, store, embedder, _, _ = indexed_app
    app = create_app(settings)
    with TestClient(app) as client:
        with client.stream("POST", "/api/chat", json={"message": "Wat is Mars?"}) as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())
        assert "event: sources" in body
        assert "event: token" in body
        assert "event: done" in body
        status = client.get("/api/status").json()
        assert status["chat"]["backend"] == "mock"
        assert status["chat"]["enabled"] is True


def test_chat_disabled_returns_503(tmp_settings):
    tmp_settings.chat_enabled = False
    app = create_app(tmp_settings)
    with TestClient(app) as client:
        res = client.post("/api/chat", json={"message": "hallo"})
        assert res.status_code == 503


def test_ui_is_chat_shell(tmp_settings):
    app = create_app(tmp_settings)
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        html = page.text
        assert 'data-mode="chat"' in html
        assert "static/style.css" in html
        assert "/static/style.css" not in html  # relatief voor HA ingress
        css = client.get("/static/style.css")
        assert css.status_code == 200
        assert "--bg" in css.text
        js = client.get("/static/app.js")
        assert js.status_code == 200
        assert "api/chat" in js.text
