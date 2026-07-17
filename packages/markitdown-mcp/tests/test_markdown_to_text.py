# SPDX-FileCopyrightText: 2024-present Adam Fourney <adamfo@microsoft.com>
#
# SPDX-License-Identifier: MIT

from markitdown_mcp.__main__ import _markdown_to_text


def test_strips_headings_and_emphasis():
    md = "# Title\n\nSome **bold** and *italic* and `code` text."
    assert _markdown_to_text(md) == "Title\n\nSome bold and italic and code text."


def test_strips_links_and_images():
    md = "See [the docs](https://example.com) and ![alt text](img.png)."
    assert _markdown_to_text(md) == "See the docs and alt text."


def test_strips_list_markers():
    md = "- one\n- two\n\n1. first\n2. second"
    assert _markdown_to_text(md) == "one\ntwo\n\nfirst\nsecond"


def test_strips_blockquotes_and_rules():
    md = "> a quote\n\n---\n\nEnd."
    assert _markdown_to_text(md) == "a quote\n\nEnd."


def test_flattens_tables():
    md = "| Name | Age |\n| ---- | --- |\n| Bob | 30 |"
    assert _markdown_to_text(md) == "Name  Age\nBob  30"


def test_keeps_code_block_contents_without_fences():
    md = "```python\nprint(1)\n```"
    assert _markdown_to_text(md) == "print(1)"


def test_output_is_no_larger_than_markdown():
    md = (
        "# Title\n\nSome **bold** and *italic* and `code` and a "
        "[link](https://example.com).\n\n- item one\n- item two\n"
    )
    text = _markdown_to_text(md)
    assert len(text) <= len(md)
