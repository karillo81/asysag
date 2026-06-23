"""Catalog of AutoSys write actions (sendevent-style) and their AEWS endpoints.

Every entry maps a stable action `key` to a verified `/AEWS/api/event/<endpoint>`
(all confirmed present + POST-capable on the live instance via OPTIONS), plus
the metadata the safety layer needs: risk tier, who may approve, what it targets,
and a plain-English summary for the human confirmation card.

Tiers (see project_job_actions_plan):
  A — reversible holds / annotations         (operator may approve)
  B — run / state control                    (operator may approve)
  C — destructive / wide blast radius        (admin only; off by default)

This module is pure data + lookup helpers; it executes nothing. The adapter
performs the POST, and Phase 2 wires the propose -> human-confirm -> execute
flow on top.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Minimum role allowed to approve each tier. Maps onto the existing
# admin/operator roles (admin is a superset of operator).
TIER_MIN_ROLE = {"A": "operator", "B": "operator", "C": "admin"}


@dataclass(frozen=True)
class JobAction:
    key: str                 # stable id used by tools/API, e.g. "on_hold"
    endpoint: str            # AEWS event path segment, e.g. "on-hold"
    label: str               # human label for the UI
    tier: str                # "A" | "B" | "C"
    target: str              # "job" | "machine" | "scheduler"
    summary: str             # plain-English effect, shown on the confirm card
    destructive: bool = False
    required_params: tuple[str, ...] = field(default_factory=tuple)

    @property
    def min_role(self) -> str:
        return TIER_MIN_ROLE[self.tier]


# --- Tier A: reversible holds / annotations -----------------------------
_TIER_A = [
    JobAction("on_hold", "on-hold", "Put on hold", "A", "job",
              "Holds the job so it won't start until taken off hold."),
    JobAction("off_hold", "off-hold", "Take off hold", "A", "job",
              "Releases a hold so the job can start normally again."),
    JobAction("on_ice", "on-ice", "Put on ice", "A", "job",
              "Ices the job: it won't run and downstream conditions treat it as absent."),
    JobAction("off_ice", "off-ice", "Take off ice", "A", "job",
              "Removes the ice so the job participates in scheduling again."),
    JobAction("on_noexec", "on-noexec", "Put on NOEXEC", "A", "job",
              "Marks the job NOEXEC: conditions evaluate but the command never runs."),
    JobAction("off_noexec", "off-noexec", "Take off NOEXEC", "A", "job",
              "Removes NOEXEC so the job executes its command again."),
    JobAction("comment", "comment", "Send comment", "A", "job",
              "Attaches an operator comment to the job's event log.",
              required_params=("comment",)),
    JobAction("change_priority", "change-priority", "Change priority", "A", "job",
              "Changes the job/application/group scheduling priority.",
              required_params=("priority",)),
    JobAction("alarm", "alarm", "Send alarm", "A", "job",
              "Raises an operator alarm against the job."),
]

# --- Tier B: run / state control ----------------------------------------
_TIER_B = [
    JobAction("start_job", "start-job", "Start job", "B", "job",
              "Starts the job now if its conditions are met."),
    JobAction("force_start_job", "force-start-job", "Force-start job", "B", "job",
              "Starts the job NOW, ignoring its starting conditions."),
    JobAction("restart_job", "restart-job", "Restart job", "B", "job",
              "Restarts the job (re-runs it)."),
    JobAction("change_status", "change-status", "Change status", "B", "job",
              "Forces the job into a specific status (e.g. SUCCESS/FAILURE).",
              required_params=("status",)),
    JobAction("reply", "reply", "Reply to job", "B", "job",
              "Answers a job that is waiting for an operator response.",
              required_params=("response",)),
    JobAction("send_signal", "send-signal", "Send signal", "B", "job",
              "Sends an OS signal to the running job process.",
              required_params=("signal",)),
    JobAction("release_resource", "release-resource", "Release resources", "B", "job",
              "Releases virtual/locked resources the job is holding."),
    JobAction("suspend", "suspend", "Suspend", "B", "job",
              "Suspends the job/application/group scheduling."),
    JobAction("resume", "resume", "Resume", "B", "job",
              "Resumes a previously suspended job/application/group."),
    JobAction("future_event", "future-event", "Send future event", "B", "job",
              "Schedules an event to fire at a future time.",
              required_params=("event", "time")),
]

# --- Tier C: destructive / wide blast radius (admin only) ----------------
_TIER_C = [
    JobAction("cancel_job", "cancel-job", "Cancel job", "C", "job",
              "Cancels the job's next scheduled run.", destructive=True),
    JobAction("kill_job", "kill-job", "Kill job", "C", "job",
              "Kills the currently running job process.", destructive=True),
    JobAction("delete_job", "delete-job", "Delete job", "C", "job",
              "PERMANENTLY deletes the job definition from AutoSys.", destructive=True),
    JobAction("machine_offline", "machine-offline", "Machine offline", "C", "machine",
              "Marks a machine offline: no jobs will be dispatched to it.",
              destructive=True),
    JobAction("machine_online", "machine-online", "Machine online", "C", "machine",
              "Brings a machine back online for job dispatch.", destructive=True),
    JobAction("stop_demon", "stop-demon", "Stop scheduler daemon", "C", "scheduler",
              "Stops the AutoSys scheduler daemon — halts ALL scheduling.",
              destructive=True),
]

ACTIONS: dict[str, JobAction] = {a.key: a for a in (_TIER_A + _TIER_B + _TIER_C)}


def get_action(key: str) -> JobAction | None:
    return ACTIONS.get(key)


def actions_for_role(role: str) -> list[JobAction]:
    """Actions a given role is permitted to approve (admin sees all)."""
    if role == "admin":
        return list(ACTIONS.values())
    return [a for a in ACTIONS.values() if a.min_role != "admin"]


def parse_allowlist(raw: str | None) -> frozenset[str]:
    """Parse the comma-separated WRITES_ALLOWLIST setting into a set of keys."""
    if not raw:
        return frozenset()
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


def is_action_allowed(key: str, allowlist: frozenset[str]) -> bool:
    """Whether an action may be proposed/executed under the current allowlist.

    Safety default: an empty allowlist permits every NON-destructive action but
    blocks all destructive ones — destructive actions must be opted in by name.
    A non-empty allowlist permits exactly the listed keys (destructive or not).
    The WRITES_ENABLED master switch is checked separately by callers.
    """
    action = get_action(key)
    if action is None:
        return False
    if action.destructive:
        return key in allowlist
    return (not allowlist) or key in allowlist


def can_approve(role: str, action: JobAction) -> bool:
    """Whether a role may approve/execute the action (admin is a superset)."""
    if action.min_role == "admin":
        return role == "admin"
    return role in ("operator", "admin")
