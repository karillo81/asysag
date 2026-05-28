from adapters.jil_parser import referenced_jobs


def test_empty_or_none_yields_empty():
    assert referenced_jobs(None) == []
    assert referenced_jobs("") == []


def test_single_success_predicate():
    assert referenced_jobs("s(etl_load_facts)") == ["etl_load_facts"]


def test_compound_and():
    assert referenced_jobs(
        "s(etl_extract_customers) AND s(etl_extract_transactions)"
    ) == ["etl_extract_customers", "etl_extract_transactions"]


def test_dedupes_and_preserves_first_seen_order():
    cond = "s(a) AND (f(b) OR done(a))"
    assert referenced_jobs(cond) == ["a", "b"]


def test_handles_failure_done_notrunning_exitcode():
    cond = "f(a) AND done(b) AND notrunning(c) AND exitcode(d, 2)"
    assert referenced_jobs(cond) == ["a", "b", "c", "d"]


def test_ignores_variable_references():
    cond = 's(etl_load_facts) AND v(MAINTENANCE_MODE, "off")'
    assert referenced_jobs(cond) == ["etl_load_facts"]


def test_tolerates_whitespace():
    assert referenced_jobs("s(  jobname  )") == ["jobname"]
