"""Safety rails: loop detection, round caps, outbound budget.

All checks live here so /agora-ask and /agora-reply share the same enforcement logic.
"""
from __future__ import annotations

import os

from . import bus, threads

DEFAULT_BUDGET_PER_HOUR = 20
DEFAULT_ROUND_CAP = 20
DEFAULT_DUP_WINDOW_SECS = 600  # 10 minutes


def _env_int(name: str, fallback: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return fallback
    try:
        n = int(raw)
        return n if n > 0 else fallback
    except ValueError:
        return fallback


def check_send(
    self_aoe_id: str,
    target_aoe_id: str,
    msg_type: str,
    body: str,
    thread_id: str | None = None,
    budget_per_hour: int = DEFAULT_BUDGET_PER_HOUR,
    round_cap: int = DEFAULT_ROUND_CAP,
    dup_window_secs: int = DEFAULT_DUP_WINDOW_SECS,
) -> tuple[bool, str]:
    """Run all safety checks. Returns (allowed, reason_if_blocked).

    Order matters: cheapest checks first, escalation-implying checks last.

    Env overrides (applied when callers pass the module defaults):
      AGORA_ROUND_CAP      — int, raises/lowers the per-thread reply cap
      AGORA_BUDGET_PER_HOUR — int, raises/lowers the hourly outbound budget
    """
    if budget_per_hour == DEFAULT_BUDGET_PER_HOUR:
        budget_per_hour = _env_int("AGORA_BUDGET_PER_HOUR", DEFAULT_BUDGET_PER_HOUR)
    if round_cap == DEFAULT_ROUND_CAP:
        round_cap = _env_int("AGORA_ROUND_CAP", DEFAULT_ROUND_CAP)
    # 1) Hourly outbound budget — cheapest, scans only recent jsonl entries
    recent = threads.recent_outbound_for(self_aoe_id, since_secs=3600)
    if len(recent) >= budget_per_hour:
        return False, (
            f"hourly outbound budget exhausted ({len(recent)}/{budget_per_hour}). "
            f"Use /agora-escalate if this is urgent, otherwise wait."
        )

    # 2) Loop detection — refuse to send a near-duplicate within the window
    incoming_hash = bus.msg_hash(target_aoe_id, msg_type, body)
    recent_dup_window = threads.recent_outbound_for(self_aoe_id, since_secs=dup_window_secs)
    for prior in recent_dup_window:
        prior_hash = bus.msg_hash(
            target_aoe_id if prior.get("to_id") is None else prior["to_id"],
            prior["msg_type"],
            prior["body"],
        )
        if prior_hash == incoming_hash:
            return False, (
                "loop detected: near-identical message sent within "
                f"the last {dup_window_secs}s. If the peer didn't respond, "
                "use /agora-escalate instead of resending."
            )

    # 3) Round cap — only applies to /reply on existing threads
    if thread_id and msg_type == "reply":
        rounds = threads.count_rounds(thread_id)
        if rounds >= round_cap:
            return False, (
                f"thread {thread_id} has reached {rounds}/{round_cap} rounds. "
                f"Per protocol, the next move MUST be /agora-escalate, not another reply."
            )

    return True, ""
