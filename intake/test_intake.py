"""Unit tests for repair_and_parse's layered JSON recovery (see CLAUDE.md
JSON-robustness contract). Run with: python3 -m pytest intake/test_intake.py -v
"""

import pytest

from intake import repair_and_parse


def test_markdown_fences_are_stripped():
    # Claude/Ollama both sometimes wrap JSON in ```json ... ``` even when told not to.
    text = '```json\n{"items": [{"name": "pallet", "qty": 2}]}\n```'
    result = repair_and_parse(text)
    assert result == {"items": [{"name": "pallet", "qty": 2}]}


def test_smart_quotes_are_normalized():
    # Some models substitute curly quotes for straight ones, which breaks json.loads outright.
    text = "“items”: [{“name”: “pallet”, “qty”: 1}]"
    text = "{" + text + "}"
    result = repair_and_parse(text)
    assert result == {"items": [{"name": "pallet", "qty": 1}]}


def test_truncated_items_array_salvages_complete_objects():
    # Simulates hitting MAX_TOKENS mid-response: last object is cut off mid-field.
    text = (
        '{"items": ['
        '{"name": "box", "qty": 3}, '
        '{"name": "pallet", "qty": 1}, '
        '{"name": "cut off", "descripti'
    )
    result = repair_and_parse(text)
    assert result["truncated"] is True
    assert result["items"] == [
        {"name": "box", "qty": 3},
        {"name": "pallet", "qty": 1},
    ]


def test_pure_garbage_raises_value_error():
    # No '{' anywhere in the text at all.
    with pytest.raises(ValueError):
        repair_and_parse("the model just said something unhelpful in prose")


def test_garbage_with_stray_brace_but_no_items_array_raises_value_error():
    # Has a '{' so the fast-path brace slice is attempted, but there's no valid
    # object, list, or well-formed [items] array to salvage from.
    with pytest.raises(ValueError):
        repair_and_parse("well, { sort of a thought here")
