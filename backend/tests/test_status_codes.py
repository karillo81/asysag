from adapters.status_codes import build_table, parse_overrides, translate


def test_known_codes_translate_to_canonical_strings():
    assert translate(1) == "RUNNING"
    assert translate(4) == "SUCCESS"
    assert translate(5) == "FAILURE"
    assert translate(7) == "TERMINATED"
    assert translate(13) == "ON_HOLD"
    assert translate(14) == "ON_ICE"


def test_numeric_string_treated_as_numeric():
    """AutoSys returns status as a string like '4' in JSON — accept both."""
    assert translate("4") == "SUCCESS"


def test_unknown_code_surfaces_as_unknown():
    assert translate(999) == "UNKNOWN(999)"


def test_bad_input_is_safe():
    assert translate(None).startswith("UNKNOWN(")
    assert translate("not-a-number").startswith("UNKNOWN(")


def test_overrides_layer_on_defaults():
    table = build_table("4=COMPLETED, 99=CUSTOM")
    assert translate(4, table) == "COMPLETED"
    assert translate(5, table) == "FAILURE"  # untouched
    assert translate(99, table) == "CUSTOM"


def test_malformed_overrides_dropped_not_raised():
    parsed = parse_overrides("4=COMPLETED,broken,xx=YY,, =NOTHING")
    assert parsed == {4: "COMPLETED"}
