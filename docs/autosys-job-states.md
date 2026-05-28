# AutoSys Job States

Reference: AutoSys Workload Automation – Job State Lifecycle.

AutoSys jobs move through a defined set of states. Operators see these in `autorep` output and in the WCC console. Understanding the state diagram is essential for interpreting agent output.

## Active states

| State | Meaning |
|---|---|
| `STARTING` | The scheduler has decided to launch the job and is contacting the remote agent. Brief — usually under 60 seconds. |
| `RUNNING` | The job process is executing on the target machine. Stays RUNNING until exit code is received. |
| `RESTART` | A previous run failed and AutoSys is preparing the next attempt under the job's `n_retrys` policy. |

## Terminal states

| State | Meaning |
|---|---|
| `SUCCESS` | Exit code matched the job's `success_codes` (default: 0). The job is considered complete. |
| `FAILURE` | Exit code did not match `success_codes`. AutoSys raises the FAILURE alarm; downstream conditional jobs that require `s(this_job)` will not run. |
| `TERMINATED` | The job was stopped externally — typically by a `sendevent -E KILLJOB` from an operator, or by hitting `term_run_time`. |

## Hold/inactive states

| State | Meaning |
|---|---|
| `INACTIVE` | The job is defined but not currently eligible to run (its starting condition has not been met, or it has no schedule). |
| `ON_HOLD` | An operator (or automation) has placed the job on hold via `sendevent -E JOB_ON_HOLD`. The job will not start until released with `JOB_OFF_HOLD`. Holds persist across scheduler restarts. |
| `ON_ICE` | Similar to `ON_HOLD` but downstream jobs evaluate as if this job were complete-and-successful. Used for jobs that are temporarily out of scope. |

## State transitions to know

- A job entering `FAILURE` blocks any downstream that depends on `s(this_job)` (success).
- A job entering `TERMINATED` is also treated as failure for downstream condition purposes — unless the downstream uses `done(this_job)` instead of `s(...)`.
- `ON_HOLD` does NOT propagate to downstream conditions automatically — use `ON_ICE` if you want the chain to flow past a held job.
- Box jobs (`job_type: BOX`) take their state from their member jobs. A box is `RUNNING` while any member is running and `FAILURE` if any member ends in FAILURE.

## See also

- ETL runbook (runbooks/etl_runbook.md) for ORA-12541 and other common job failures
