"""Extract job references from a JIL `condition:` expression.

We don't need to evaluate the boolean semantics — for the agent's purposes,
"what is this job dependent on" is the set of job names referenced anywhere
in the expression. A focused regex over the predicate forms is enough.

Supported predicates (from the AutoSys conditions docs):
    s(job)          success
    f(job)          failure
    done(job)       done (any terminal status)
    notrunning(job) not currently running
    exitcode(job,N) exit code N
    success(job)    (synonym for s)
    failure(job)    (synonym for f)

Out of scope:
    v(global_var, "value")  — references variables, not jobs
    AND / OR / NOT / parens — boolean wiring, ignored for the ref extraction
"""

from __future__ import annotations

import re

# Capture the first argument of any of the predicate forms above. Whitespace
# is allowed inside the parens, e.g. `s( jobname )`. Job names are conservative:
# letters/digits/underscore/dot/dash, no whitespace.
_PRED = re.compile(
    r"\b(?:s|f|done|notrunning|success|failure|exitcode)\s*\(\s*"
    r"([A-Za-z0-9_.\-]+)",
)


def referenced_jobs(condition: str | None) -> list[str]:
    """Return the deduplicated, order-preserving list of job names referenced
    in the condition expression. Empty list for empty/None input."""
    if not condition:
        return []
    seen: dict[str, None] = {}
    for match in _PRED.finditer(condition):
        name = match.group(1)
        seen.setdefault(name, None)
    return list(seen)
