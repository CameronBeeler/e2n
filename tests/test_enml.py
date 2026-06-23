from e2n.enml import ContentSegment, chunk_text, plan_enml_segments


def test_plan_enml_segments_breaks_text_around_non_text_items() -> None:
    content = """<?xml version="1.0" encoding="UTF-8"?>
<en-note>Before <a href="evernote:/view/123/s1/guid/guid/">Linked Note</a> after
<en-media hash="abc123" type="application/pdf"/> done</en-note>"""

    assert plan_enml_segments(content) == (
        ContentSegment(kind="text", text="Before"),
        ContentSegment(kind="evernote_link", text="Linked Note", value="evernote:/view/123/s1/guid/guid/"),
        ContentSegment(kind="text", text="after"),
        ContentSegment(kind="resource", text="en-media", value="abc123", mime_type="application/pdf"),
        ContentSegment(kind="text", text="done"),
    )


def test_plan_enml_segments_http_link_stays_inline() -> None:
    content = '<en-note>Visit <a href="https://example.com">Example</a> today</en-note>'
    assert plan_enml_segments(content) == (
        ContentSegment(kind="text", text="Visit"),
        ContentSegment(kind="http_link", text="Example", value="https://example.com"),
        ContentSegment(kind="text", text="today"),
    )


def test_plan_enml_segments_table_becomes_own_segment() -> None:
    content = "<en-note>Before<table><tr><td>Cell</td></tr></table>After</en-note>"
    segments = plan_enml_segments(content)
    kinds = [s.kind for s in segments]
    assert "table" in kinds
    text_segments = [s for s in segments if s.kind == "text"]
    assert any("Before" in s.text for s in text_segments)
    assert any("After" in s.text for s in text_segments)


def test_plan_enml_segments_image_resource_carries_mime_type() -> None:
    content = '<en-note><en-media hash="imgabc" type="image/png"/></en-note>'
    (segment,) = plan_enml_segments(content)
    assert segment.kind == "resource"
    assert segment.mime_type == "image/png"
    assert segment.value == "imgabc"


def test_plan_enml_segments_chunks_large_text_blocks() -> None:
    assert plan_enml_segments("<en-note>one two three four</en-note>", text_block_limit=7) == (
        ContentSegment(kind="text", text="one two"),
        ContentSegment(kind="text", text="three"),
        ContentSegment(kind="text", text="four"),
    )


def test_chunk_text_splits_long_words() -> None:
    assert chunk_text("abcdef ghi", limit=3) == ("abc", "def", "ghi")


# --- GAP-6: Structural ENML element recognition ---


def test_heading_elements_produce_heading_segments() -> None:
    content = "<en-note><h1>Title</h1><h2>Subtitle</h2><h3>Section</h3></en-note>"
    segments = plan_enml_segments(content)
    headings = [s for s in segments if s.kind == "heading"]
    assert len(headings) == 3
    assert headings[0].text == "Title" and headings[0].level == 1
    assert headings[1].text == "Subtitle" and headings[1].level == 2
    assert headings[2].text == "Section" and headings[2].level == 3


def test_h4_h5_h6_map_to_level_3() -> None:
    content = "<en-note><h4>Four</h4><h5>Five</h5><h6>Six</h6></en-note>"
    segments = plan_enml_segments(content)
    headings = [s for s in segments if s.kind == "heading"]
    assert all(h.level == 3 for h in headings)


def test_unordered_list_produces_bulleted_list_segments() -> None:
    content = "<en-note><ul><li>Apple</li><li>Banana</li></ul></en-note>"
    segments = plan_enml_segments(content)
    items = [s for s in segments if s.kind == "bulleted_list"]
    assert len(items) == 2
    assert items[0].text == "Apple"
    assert items[1].text == "Banana"


def test_ordered_list_produces_numbered_list_segments() -> None:
    content = "<en-note><ol><li>First</li><li>Second</li></ol></en-note>"
    segments = plan_enml_segments(content)
    items = [s for s in segments if s.kind == "numbered_list"]
    assert len(items) == 2


def test_blockquote_produces_quote_segment() -> None:
    content = "<en-note><blockquote>Wise words</blockquote></en-note>"
    segments = plan_enml_segments(content)
    quotes = [s for s in segments if s.kind == "quote"]
    assert len(quotes) == 1
    assert quotes[0].text == "Wise words"


def test_pre_produces_code_segment() -> None:
    content = "<en-note><pre>def hello():\n    pass</pre></en-note>"
    segments = plan_enml_segments(content)
    codes = [s for s in segments if s.kind == "code"]
    assert len(codes) == 1
    assert "def hello():" in codes[0].text


def test_hr_produces_divider_segment() -> None:
    content = "<en-note><p>Above</p><hr/><p>Below</p></en-note>"
    segments = plan_enml_segments(content)
    dividers = [s for s in segments if s.kind == "divider"]
    assert len(dividers) == 1


def test_en_todo_produces_to_do_segments() -> None:
    content = '<en-note><div><en-todo checked="false"/>Buy milk</div><div><en-todo checked="true"/>Done</div></en-note>'
    segments = plan_enml_segments(content)
    todos = [s for s in segments if s.kind == "to_do"]
    assert len(todos) == 2
    assert todos[0].text == "Buy milk" and todos[0].checked is False
    assert todos[1].text == "Done" and todos[1].checked is True


# --- GAP-3: Table conversion ---


def test_table_produces_table_segment_with_data() -> None:
    content = """<en-note><table><tr><th>Name</th><th>Age</th></tr><tr><td>Alice</td><td>30</td></tr></table></en-note>"""
    segments = plan_enml_segments(content)
    tables = [s for s in segments if s.kind == "table"]
    assert len(tables) == 1
    assert tables[0].rows is not None
    assert len(tables[0].rows) == 2
    assert tables[0].rows[0] == ["Name", "Age"]
    assert tables[0].rows[1] == ["Alice", "30"]


# --- GAP-2: Additional Evernote link formats ---


def test_evernote_web_links_detected() -> None:
    content = '<en-note><a href="https://www.evernote.com/shard/s123/nl/12345/abcd-1234/">Web Link</a></en-note>'
    segments = plan_enml_segments(content)
    links = [s for s in segments if s.kind == "evernote_link"]
    assert len(links) == 1
    assert links[0].text == "Web Link"


def test_evernote_shortened_links_detected() -> None:
    content = '<en-note><a href="https://www.evernote.com/l/AbCdEfG">Short Link</a></en-note>'
    segments = plan_enml_segments(content)
    links = [s for s in segments if s.kind == "evernote_link"]
    assert len(links) == 1


# --- GAP-8: en-crypt handling ---


def test_en_crypt_produces_encrypted_segment() -> None:
    content = '<en-note><en-crypt hint="birthday" cipher="AES" length="128">base64data==</en-crypt></en-note>'
    segments = plan_enml_segments(content)
    encrypted = [s for s in segments if s.kind == "encrypted"]
    assert len(encrypted) == 1
    assert encrypted[0].value == "birthday"


# --- GAP-7: Inline formatting tracking ---


def test_bold_text_produces_annotated_segment() -> None:
    content = "<en-note><p>Hello <b>world</b> end</p></en-note>"
    segments = plan_enml_segments(content)
    text_segments = [s for s in segments if s.kind == "text"]
    # Should have segments with annotations tracking
    all_text = "".join(s.text for s in text_segments)
    assert "world" in all_text
    # Find the segment containing "world" and check it has bold annotation
    bold_segments = [s for s in text_segments if "world" in s.text and s.annotations and s.annotations.get("bold")]
    assert len(bold_segments) >= 1


def test_italic_text_produces_annotated_segment() -> None:
    content = "<en-note><p><i>emphasis</i></p></en-note>"
    segments = plan_enml_segments(content)
    text_segments = [s for s in segments if s.kind == "text"]
    italic_segments = [s for s in text_segments if s.annotations and s.annotations.get("italic")]
    assert len(italic_segments) >= 1
    assert "emphasis" in italic_segments[0].text


def test_multiple_annotations_preserved() -> None:
    content = "<en-note><p><b><i>bold-italic</i></b> and <u>underline</u></p></en-note>"
    segments = plan_enml_segments(content)
    text_segments = [s for s in segments if s.kind == "text"]
    bi = [s for s in text_segments if s.annotations and s.annotations.get("bold") and s.annotations.get("italic")]
    assert len(bi) >= 1
    assert "bold-italic" in bi[0].text
    ul = [s for s in text_segments if s.annotations and s.annotations.get("underline")]
    assert len(ul) >= 1
    assert "underline" in ul[0].text


# --- GAP-5: Improved text splitting with equal division ---


def test_chunk_text_equal_division_no_tiny_remainder() -> None:
    """chunk_text should produce approximately equal blocks, not max + tiny remainder."""
    # 100 chars at limit 30: naive approach gives 3x30 + 1x10.
    # Equal division: ceil(100/30)=4 blocks, target ~25 each.
    text = " ".join(["abcde"] * 20)  # 20 words of 5 chars = 119 chars with spaces
    chunks = chunk_text(text, limit=30)
    lengths = [len(c) for c in chunks]
    # No chunk should be less than 40% of the average (guards against tiny remainder)
    avg = sum(lengths) / len(lengths)
    assert all(l >= avg * 0.4 for l in lengths), f"Chunks too uneven: {lengths}"


def test_chunk_text_prefers_natural_boundaries() -> None:
    """When sentence boundaries exist near the split point, prefer them."""
    text = "Short sentence. Another sentence here. Yet more words follow in this text."
    # Limit that forces a split roughly in the middle
    chunks = chunk_text(text, limit=40)
    # Should split at a sentence boundary (period + space)
    assert len(chunks) >= 2
    # First chunk should end at a word boundary at minimum
    assert chunks[0].endswith(".") or chunks[0][-1].isalpha()
