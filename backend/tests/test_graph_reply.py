from agent.graph import _flatten_content


def test_flatten_string_content():
    assert _flatten_content("hello") == "hello"


def test_flatten_none_content():
    assert _flatten_content(None) == ""


def test_flatten_list_of_parts():
    content = [{"type": "text", "text": "No, "}, {"type": "text", "text": "test9 failed."}]
    assert _flatten_content(content) == "No, test9 failed."


def test_flatten_list_with_plain_strings():
    assert _flatten_content(["a", "b"]) == "ab"
