# AutoSys Conditions and Dependencies

Conditions are how AutoSys jobs declare what must be true before they can start. They appear in the `condition:` field of JIL and drive the dependency graph operators reason about.

## Basic predicates

| Predicate | Means |
|---|---|
| `s(job_name)` | job_name finished SUCCESS in the current cycle |
| `f(job_name)` | job_name finished FAILURE in the current cycle |
| `done(job_name)` | job_name finished — SUCCESS, FAILURE, or TERMINATED |
| `notrunning(job_name)` | job_name is not currently in RUNNING state |
| `v(global_var, "value")` | global variable equals value |
| `exitcode(job_name, N)` | last exit code was N |

## Boolean combinators

`AND`, `OR`, `NOT` — standard precedence, parentheses for grouping.

Examples:

```
condition: s(etl_extract_customers) AND s(etl_extract_transactions)
condition: s(etl_load_facts) OR exitcode(etl_load_facts, 2)
condition: NOT f(upstream_check)
```

## Boxes vs CMD jobs

A `BOX` job groups CMD jobs and provides them a shared starting condition. The members inherit the box's start time and run when their *own* conditions are met within the box's window.

```
insert_job: etl_box_daily      job_type: BOX
   start_times: "03:00"
   condition: s(prev_etl_box_daily)

insert_job: etl_extract_customers  job_type: CMD
   box_name: etl_box_daily
   command: /opt/etl/bin/extract.sh customers
```

When the box starts, `etl_extract_customers` starts immediately (no internal condition). Downstream box members typically declare conditions referencing each other (`s(etl_extract_customers)`).

## Cycles

Conditions implicitly reset each cycle. By default a cycle aligns with the calendar day (00:00 to 23:59 local), but `date_conditions` and `run_calendar` can extend that. Operators investigating "why didn't X run today?" should always check whether the cycle has actually advanced — a long-running upstream from yesterday will hold the cycle open.

## Inspecting the graph

- `autorep -J <name> -d` — print the job definition including conditions.
- `autorep -J <name> -q` — full JIL for the job.
- The REST endpoint `GET /jobs/{name}/dependencies` returns parsed upstream + downstream lists for tools that don't want to walk JIL text themselves.
