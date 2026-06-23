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
    endpoint: str            # AEWS event path segment under /AEWS/event/
    label: str               # human label for the UI
    tier: str                # "A" | "B" | "C"
    target: str              # "job" | "machine" | "scheduler"
    summary: str             # plain-English effect, shown on the confirm card
    destructive: bool = False
    required_params: tuple[str, ...] = field(default_factory=tuple)
    # Agent-facing param name -> AEWS body field name (e.g. status -> jobStatus).
    param_map: dict = field(default_factory=dict)
    # True once the endpoint path is confirmed against a live AEWS. Unverified
    # actions are blocked everywhere (never proposed or executed) until checked.
    verified: bool = True

    @property
    def min_role(self) -> str:
        return TIER_MIN_ROLE[self.tier]


# Endpoints verified against the live AEWS (POST /AEWS/event/<endpoint>,
# success = HTTP 201). Unverified entries (verified=False) carry a best-guess
# path and stay blocked until confirmed — see is_action_allowed.

# --- Tier A: reversible holds / annotations -----------------------------
_TIER_A = [
    JobAction("on_hold", "hold-job", "Put on hold", "A", "job",
              "Holds the job so it won't start until taken off hold."),
    JobAction("off_hold", "off-hold-job", "Take off hold", "A", "job",
              "Releases a hold so the job can start normally again."),
    JobAction("on_ice", "ice-job", "Put on ice", "A", "job",
              "Ices the job: it won't run and downstream conditions treat it as absent."),
    JobAction("off_ice", "off-ice-job", "Take off ice", "A", "job",
              "Removes the ice so the job participates in scheduling again."),
    JobAction("on_noexec", "noexec-job", "Put on NOEXEC", "A", "job",
              "Marks the job NOEXEC: conditions evaluate but the command never runs."),
    JobAction("off_noexec", "off-noexec-job", "Take off NOEXEC", "A", "job",
              "Removes NOEXEC so the job executes its command again."),
    JobAction("comment", "comment-job", "Send comment", "A", "job",
              "Attaches an operator comment to the job's event log.",
              required_params=("comment",), verified=False),
    JobAction("change_priority", "change-priority-job", "Change priority", "A", "job",
              "Changes the job scheduling priority.",
              required_params=("priority",), verified=False),
    JobAction("alarm", "alarm-job", "Send alarm", "A", "job",
              "Raises an operator alarm against the job.", verified=False),
]

# --- Tier B: run / state control ----------------------------------------
_TIER_B = [
    JobAction("start_job", "start-job", "Start job", "B", "job",
              "Starts the job now if its conditions are met."),
    JobAction("force_start_job", "force-start-job", "Force-start job", "B", "job",
              "Starts the job NOW, ignoring its starting conditions."),
    JobAction("restart_job", "restart-job", "Restart job", "B", "job",
              "Restarts the job (re-runs it)."),
    JobAction("change_status", "change-status-job", "Change status", "B", "job",
              "Forces the job into a specific status (e.g. SUCCESS/FAILURE).",
              required_params=("status",), param_map={"status": "jobStatus"}),
    JobAction("reply", "reply-job", "Reply to job", "B", "job",
              "Answers a job that is waiting for an operator response.",
              required_params=("response",)),
    JobAction("send_signal", "send-signal-job", "Send signal", "B", "job",
              "Sends an OS signal to the running job process.",
              required_params=("signal",)),
    JobAction("release_resource", "release-resources-job", "Release resources", "B", "job",
              "Releases virtual/locked resources the job is holding."),
    JobAction("suspend", "suspend-job", "Suspend", "B", "job",
              "Suspends the job/application/group scheduling."),
    JobAction("resume", "resume-job", "Resume", "B", "job",
              "Resumes a previously suspended job/application/group."),
    JobAction("future_event", "future-event-job", "Send future event", "B", "job",
              "Schedules an event to fire at a future time.",
              required_params=("event", "time"), verified=False),
]

# --- Tier C: destructive / wide blast radius (admin only) ----------------
_TIER_C = [
    JobAction("cancel_job", "cancel-job", "Cancel job", "C", "job",
              "Cancels the job's next scheduled run.", destructive=True),
    JobAction("kill_job", "kill-job", "Kill job", "C", "job",
              "Kills the currently running job process.",
              destructive=True, verified=False),
    JobAction("delete_job", "delete-job", "Delete job", "C", "job",
              "PERMANENTLY deletes the job definition from AutoSys.",
              destructive=True, verified=False),
    JobAction("machine_offline", "machine-offline", "Machine offline", "C", "machine",
              "Marks a machine offline: no jobs will be dispatched to it.",
              destructive=True, verified=False),
    JobAction("machine_online", "machine-online", "Machine online", "C", "machine",
              "Brings a machine back online for job dispatch.",
              destructive=True, verified=False),
    JobAction("stop_demon", "stop-demon", "Stop scheduler daemon", "C", "scheduler",
              "Stops the AutoSys scheduler daemon — halts ALL scheduling.",
              destructive=True, verified=False),
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
    if not action.verified:
        return False  # endpoint path not confirmed against live AEWS yet
    if action.destructive:
        return key in allowlist
    return (not allowlist) or key in allowlist


def can_approve(role: str, action: JobAction) -> bool:
    """Whether a role may approve/execute the action (admin is a superset)."""
    if action.min_role == "admin":
        return role == "admin"
    return role in ("operator", "admin")
