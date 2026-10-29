from kippiesearch.chunking import chunk_text


def test_chunk_overlap_and_order():
    text = ("paragraaf een. " * 40) + "\n\n" + ("paragraaf twee. " * 40)
    chunks = chunk_text(text, chunk_chars=120, overlap=30)
    assert len(chunks) >= 2
    assert chunks[0].index == 0
    assert chunks[-1].end <= len(text.strip())
    # overlap: start van chunk 1 ligt voor einde van chunk 0
    assert chunks[1].start < chunks[0].end


def test_empty_text():
    assert chunk_text("   ") == []
    assert chunk_text("") == []
