# AutoSys `sendevent` Command Reference

`sendevent` is the operator's primary tool for changing job state or triggering actions in AutoSys. Aliases vary by version; in recent releases the equivalent REST endpoint is `POST /jobs/{name}/events`.

## Common events

### `FORCE_STARTJOB`

```
sendevent -E FORCE_STARTJOB -J <job_name>
```

Forces a job to start *now*, regardless of its starting condition. Used to retry a failed job after the underlying issue is fixed. Does NOT skip the box's other conditions.

### `JOB_ON_HOLD`

```
sendevent -E JOB_ON_HOLD -J <job_name>
```

Marks the job as `ON_HOLD`. The job will not be scheduled until released. Common during incident response when an upstream is broken and you want to prevent cascading retries.

### `JOB_OFF_HOLD`

```
sendevent -E JOB_OFF_HOLD -J <job_name>
```

Releases a held job. It will start at its next eligible window (or immediately if conditions are already met).

### `JOB_ON_ICE` / `JOB_OFF_ICE`

```
sendevent -E JOB_ON_ICE -J <job_name>
sendevent -E JOB_OFF_ICE -J <job_name>
```

Like hold/off-hold, but downstream conditions evaluate the iced job as successful. Use when you need a chain to flow past a job that is intentionally not running today.

### `KILLJOB`

```
sendevent -E KILLJOB -J <job_name>
```

Stops a running job immediately. The job moves to `TERMINATED`. Downstream `s(...)` conditions will fail; use `done(...)` if the chain should still flow.

### `CHANGE_STATUS`

```
sendevent -E CHANGE_STATUS -s SUCCESS -J <job_name>
```

Manually overrides the job's status. **Dangerous** — bypasses the actual run. Reserved for cases where the job exit was lost (agent crash) but the work is known to have completed. Always log the manual override.

## Audit and accountability

Every `sendevent` call is recorded in the AutoSys event log and is visible via `autorep -d <job_name>`. Include a comment in your incident ticket when you issue one — operators downstream of you need to see what was overridden and why.
