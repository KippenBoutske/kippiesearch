from kippiesearch.mcp_server import build_mcp
from kippiesearch.search import SearchService
from kippiesearch.sources import resolve_safe_path


def test_mcp_tools_search_and_sources(indexed_app):
    settings, store, embedder, _, _ = indexed_app
    searcher = SearchService(store, embedder)
    mcp = build_mcp(settings, store, searcher)
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    assert set(tools) >= {"search", "list_sources", "get_file_content"}

    hits = tools["search"].fn("Mars planeet")
    assert hits
    sources = tools["list_sources"].fn()
    assert sources["files"] >= 3
    path = hits[0]["path"]
    resolved = resolve_safe_path(settings, path)
    assert resolved.exists()
    content = tools["get_file_content"].fn(path, 500)
    assert content["text"]
    assert content["path"] == path
