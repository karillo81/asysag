# ETL Runbook

Operational reference for the nightly `etl_box_daily` and morning `reporting_box_morning` boxes. Last reviewed 2026-05-01.

---

## ORA-12541: TNS:no listener {#ora-12541}

**Symptom:** Job fails immediately (or after a short connect timeout) with `ORA-12541: TNS:no listener` referencing a database host such as `db-warehouse-prod-01.internal:1521`.

**Likely causes (most common first):**
- Oracle TNS listener service is stopped on the target host (often after maintenance windows).
- Host reachable but listener crashed or hung.
- Network ACL / firewall change blocking port 1521.

**Resolution:**
1. SSH to the target DB host (e.g. `db-warehouse-prod-01.internal`).
2. Check listener state: `lsnrctl status`.
3. If down: `lsnrctl start`. Verify it reports the expected service names.
4. From the ETL host, confirm reachability: `tnsping warehouse_prod`.
5. Retry the failed job: `sendevent -E FORCE_STARTJOB -J etl_load_facts`.

**Escalation:** Page DBA on-call if listener cannot be started, host is unreachable, or if the issue recurs within 24h.

**SLA:** Restore by **06:00** to avoid morning report impact (reporting_box_morning starts at 06:00).

---

## ORA-01653: unable to extend table in tablespace {#ora-01653}

**Symptom:** Fact or dimension load fails with `ORA-01653: unable to extend table <X> by <N> in tablespace <NAME>`.

**Likely cause:** Tablespace is full or autoextend has hit its max.

**Resolution:**
1. Confirm tablespace usage: query `dba_data_files` / `dba_free_space`.
2. DBA adds space to the tablespace (or extends datafile maxsize).
3. Retry the failed job.

**Escalation:** Always involve DBA — adding space requires their access.

---

## Connection timeouts to upstream APIs {#connection-timeouts}

**Symptom:** Extract jobs fail with `connection timed out` to a known-good source.

**Likely causes:**
- Source-side outage (check their status page first).
- Stuck connection pool on the ETL host.
- DNS or routing change.

**Resolution:**
1. Test reachability from `etl-prod-01.internal`: `curl -v https://<host>/health` or equivalent.
2. If reachable: restart the connection pool service on the ETL host.
3. If unreachable: contact the source team / network on-call.
4. Retry the failed job.

---

## Job retry conventions

- **Manual retry of a single CMD job:** `sendevent -E FORCE_STARTJOB -J <job_name>`.
- **Manual retry of an entire box:** `sendevent -E FORCE_STARTJOB -J <box_name>` (re-runs in-box jobs per their conditions).
- Always record the retry in the incident ticket — include the original error and the resolution applied.
