"""
Necromance — raise a dead claude session to consult its accumulated knowledge.

A necromancy resurrects a dormant ``<uuid>.jsonl`` as a live AoE session under
the dedicated ``graveyard`` profile (kept out of the default dashboard and
exempt from lazarus auto-nudges), pipes a question to it via the standard
agora peer-msg machinery, and culls it after a 5-minute idle TTL.

Read-only by convention: the necromancy preamble instructs the resurrected
agent not to modify files, push, or run side-effecting commands. Soft guarantee
— claude has no read-only mode — but with xhigh effort the framing is followed.

Composition note: this module is built ENTIRELY on top of primitives shipped
in PR #1 (placeholder-aware ``input_is_empty``, ``force_send=True`` on
``aoe_send_peer_msg``, and ``wait_for_pane_ready``). Without those, cold-start
delivery into a freshly-resumed pane would silently drop the question.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import bus, graveyard, peer_msg as pm, inbox


# ---------- Constants ----------

PROFILE = "graveyard"
PROFILE_DIR = bus.AOE_CONFIG / "profiles" / PROFILE
PROFILE_SESSIONS_JSON = PROFILE_DIR / "sessions.json"

DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_EFFORT = "xhigh"
DEFAULT_TTL_SECONDS = 300  # 5 minutes idle before cull

# When claude resumes a session it shows an interactive prompt asking whether
# to load the full transcript or a summary. We auto-pick the full path —
# fidelity is what you came to a dead session for. Summary-mode is available
# behind a flag.
RESUME_PROMPT_MARKERS = (
    "Resume from summary",
    "Resume full session",
)
RESUME_FULL_KEY = "2"
RESUME_SUMMARY_KEY = "1"

# Bounded waits.
PANE_READY_TIMEOUT_S = 20.0   # claude full-resume can take up to ~12s on big sessions
RESUME_PROMPT_TIMEOUT_S = 25.0

# Provisional locks (status="launching") older than this are assumed orphaned
# (process died before promoting to status="live") and are eligible for cull
# alongside ttl-expired live locks.
LAUNCH_TIMEOUT_SECONDS = 60


# ---------- Lock + lifecycle ----------

STATUS_LAUNCHING = "launching"
STATUS_LIVE = "live"


@dataclass
class Necromancy:
    """An active necromantic resurrection."""
    uuid: str            # dead session UUID being resurrected
    label: str
    aoe_id: str          # the FRESH AoE session id (under graveyard profile)
    title: str           # the AoE session title (necro-<label>-<short>)
    started_at: float
    last_active: float
    thread_id: str       # agora thread carrying the consultation
    status: str = STATUS_LIVE  # "launching" until preamble lands, then "live"

    def lock_path(self) -> Path:
        return graveyard.LIVE_DIR / f"{self.uuid}.lock"

    def _serialize(self) -> str:
        return json.dumps({
            "uuid": self.uuid,
            "label": self.label,
            "aoe_id": self.aoe_id,
            "title": self.title,
            "started_at": self.started_at,
            "last_active": self.last_active,
            "thread_id": self.thread_id,
            "status": self.status,
        })

    def write_lock(self) -> None:
        """Atomic write via tmp + replace. Concurrent readers either see the
        old contents or the new contents — never a partial-write corruption."""
        graveyard.ensure_graveyard_root()
        path = self.lock_path()
        tmp = path.with_suffix(".lock.tmp")
        tmp.write_text(self._serialize())
        tmp.replace(path)

    def claim_lock(self) -> bool:
        """Atomically create the lock file. Returns True if WE created it,
        False if another necromancy already holds it.

        Uses ``open(O_CREAT | O_EXCL | O_WRONLY)`` so two concurrent
        ``agora necromance <same-uuid>`` callers serialize via the filesystem
        — only one wins the spawn race; the other rolls back to follow-up.
        """
        graveyard.ensure_graveyard_root()
        path = self.lock_path()
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        try:
            os.write(fd, self._serialize().encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def touch(self) -> None:
        """Refresh ``last_active`` to now. Called when a follow-up arrives."""
        self.last_active = time.time()
        self.write_lock()

    def promote_to_live(self) -> None:
        """Flip ``launching`` → ``live`` after the consultation peer-msg has
        been delivered. Cull treats launching locks past LAUNCH_TIMEOUT_SECONDS
        as orphaned (caller died mid-spawn)."""
        self.status = STATUS_LIVE
        self.write_lock()


def load_lock(uuid: str) -> Optional[Necromancy]:
    p = graveyard.LIVE_DIR / f"{uuid}.lock"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        return Necromancy(**d)
    except (json.JSONDecodeError, OSError, TypeError):
        return None


def list_live() -> list[Necromancy]:
    """All currently live necromancies (one per dead-uuid)."""
    graveyard.ensure_graveyard_root()
    out = []
    for p in graveyard.LIVE_DIR.glob("*.lock"):
        n = load_lock(p.stem)
        if n is not None:
            out.append(n)
    return out


# ---------- Preamble ----------

def _preamble(caller_label: str) -> str:
    return f"""🕯 You have been raised from the dead by {caller_label!r} for the knowledge in this conversation.

READ-ONLY MODE (soft):
  - DO NOT modify files (no Write, no Edit, no NotebookEdit)
  - DO NOT run side-effecting commands (no git push, no gh pr {{create,merge,close}},
    no curl with -X POST/PUT/DELETE, no installs, no rm)
  - DO NOT use TaskCreate/TodoWrite (state isn't relevant to a single consultation)

YOU MAY:
  - Re-read your own transcript and any files referenced in it (Read, Grep, Glob)
  - Shell out for read-only inspection (cat, grep, find, gh view, git log/show/diff)
  - Use WebFetch for facts you originally cited

REPLY VIA `/agora-reply {{thread}} <answer>`. Be concrete — your caller has a
question that needs the SPECIFIC knowledge you accumulated, not a generic answer.

After your reply you will be released. If asked a follow-up within 5 minutes,
answer the same way. If you're unsure of the answer, SAY SO — a calibrated
"I don't remember" is more useful than a confabulated yes.
"""


# ---------- Profile bootstrap ----------

def _ensure_profile() -> None:
    """Idempotent: make sure the ``graveyard`` AoE profile directory exists.

    AoE itself creates the profile on first use of ``--profile graveyard`` but
    we touch sessions.json explicitly so reading it before any add never racing
    against AoE's lazy init.
    """
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    if not PROFILE_SESSIONS_JSON.exists():
        PROFILE_SESSIONS_JSON.write_text(json.dumps({"sessions": []}))


def _patch_session_record(aoe_id: str, target_uuid: str, model: str, effort: str) -> bool:
    """Patch the freshly-added session record so ``aoe session start`` resumes
    the correct claude jsonl with the requested model + effort.

    Returns True on success.

    The read-modify-write is serialized via ``fcntl.flock`` on a sidecar
    lockfile to avoid clobbering concurrent AoE writes to the profile
    sessions.json (status/last_accessed updates). flock is advisory, so this
    only works if every writer participates — AoE itself does not, but in the
    graveyard profile there are no AoE-side pollers writing to sessions.json
    in the background, so the practical race window is just AoE's own session
    lifecycle writes during ``aoe session start``, which serialize behind
    our flock as long as both processes obey it.
    """
    if not PROFILE_SESSIONS_JSON.exists():
        return False
    lock_path = PROFILE_SESSIONS_JSON.with_suffix(".flock")
    try:
        with lock_path.open("w") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                data = json.loads(PROFILE_SESSIONS_JSON.read_text())
            except (json.JSONDecodeError, OSError):
                return False
            sessions = data if isinstance(data, list) else data.get("sessions", [])
            for s in sessions:
                if s.get("id") == aoe_id:
                    s["extra_args"] = (
                        f"--resume {target_uuid} --model {model} --effort {effort} "
                        f"--dangerously-skip-permissions"
                    )
                    s["agent_session_id"] = target_uuid
                    tmp = PROFILE_SESSIONS_JSON.with_suffix(".tmp")
                    tmp.write_text(json.dumps(data, indent=2))
                    tmp.replace(PROFILE_SESSIONS_JSON)
                    return True
            return False
    except OSError:
        return False


# ---------- Spawn + dispatch ----------

def _aoe(cmd: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    """Run an ``aoe`` subcommand under the graveyard profile, return result.

    Profile-scoping is forced via env var so even a typo in subcommand args
    can't accidentally hit the default profile.
    """
    env = dict(os.environ)
    env["AGENT_OF_EMPIRES_PROFILE"] = PROFILE
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)


def _short(uuid: str) -> str:
    return uuid[:8]


def _make_title(label: str, uuid: str) -> str:
    """Generate a unique-ish AoE session title. Bounded to AoE's allowed chars."""
    base = re.sub(r"[^\w\-. ]+", "-", label)[:32]
    return f"necro-{base}-{_short(uuid)}"


def necromance(
    caller: bus.SessionIdentity,
    label_or_uuid: str,
    question: str,
    *,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    summary_mode: bool = False,
    project_path: Optional[str] = None,
) -> tuple[bool, str, Optional[Necromancy]]:
    """Raise a dead session and ask it ``question``. Returns (ok, message, necro).

    If a live necromancy of the same UUID already exists, it's reused (the
    question is sent as a follow-up on a NEW agora thread) and the lock
    timestamp is refreshed.

    Does NOT block waiting for the reply — the resurrected session writes
    back via /agora-reply on its own clock. The caller's inbox picks up the
    response just like any peer-msg.
    """
    if not question.strip():
        return False, "empty question", None

    entry = graveyard.resolve(label_or_uuid)
    if entry is None:
        return False, (
            f"no dead session matches {label_or_uuid!r}. "
            f"Try `agora grave-dig <query>` to search by content."
        ), None

    # MED-4: refuse to fork a currently-live AoE session — a second --resume
    # against the same jsonl produces undefined claude behaviour (parallel
    # writers to one transcript). The default profile's sessions.json carries
    # `agent_session_id` for every live AoE-tracked claude.
    if _is_live_in_default_profile(entry.uuid):
        return False, (
            f"{entry.label} is currently live in your default AoE profile. "
            f"Use `/agora-ask <label>` (or its tab) to consult it directly — "
            f"don't necromance a still-running session."
        ), None

    # Reuse a live resurrection if one's already up for this UUID.
    existing = load_lock(entry.uuid)
    if existing is not None and _tmux_alive(existing.aoe_id):
        return _send_follow_up(caller, existing, question)

    # HIGH-1/HIGH-3: claim the lock provisionally BEFORE the long spawn flow.
    # claim_lock uses O_EXCL semantics so two concurrent necromance() calls
    # for the same UUID serialize here — the loser sees the winner's lock and
    # routes to follow-up below. The lock is `status=launching` so cull can
    # garbage-collect it if we crash mid-spawn (LAUNCH_TIMEOUT_SECONDS).
    now = time.time()
    provisional = Necromancy(
        uuid=entry.uuid, label=entry.label,
        aoe_id="",  # filled in after aoe add
        title="",
        started_at=now, last_active=now,
        thread_id="",
        status=STATUS_LAUNCHING,
    )
    if not provisional.claim_lock():
        # Lost the race — another caller is mid-spawn. Try follow-up.
        existing = load_lock(entry.uuid)
        if existing is not None and _tmux_alive(existing.aoe_id):
            return _send_follow_up(caller, existing, question)
        return False, (
            f"another necromancy of {entry.label} is in progress — "
            f"try again in a moment"
        ), None

    # Fresh resurrection path.
    _ensure_profile()
    title = _make_title(entry.label, entry.uuid)
    project_path = project_path or os.getcwd()

    def _rollback(reason_msg: str) -> tuple[bool, str, None]:
        """On any spawn-flow failure, drop the provisional lock so cull
        doesn't have to wait LAUNCH_TIMEOUT_SECONDS to clean it up."""
        try:
            provisional.lock_path().unlink()
        except FileNotFoundError:
            pass
        return False, reason_msg, None

    add = _aoe([
        "aoe", "add", "--profile", PROFILE,
        "--title", title,
        "--cmd", "claude",
        "--yolo",
        project_path,
    ])
    if add.returncode != 0:
        return _rollback(f"aoe add failed: {(add.stderr or add.stdout)[:200]}")

    m = re.search(r"ID:\s+([0-9a-f]{12,})", add.stdout)
    if not m:
        return _rollback(f"could not parse aoe-id from output:\n{add.stdout[:300]}")
    aoe_id = m.group(1)

    # Update the provisional lock with the now-known aoe_id + title so cull
    # can clean it up if we die before promotion.
    provisional.aoe_id = aoe_id
    provisional.title = title
    provisional.last_active = time.time()
    provisional.write_lock()

    if not _patch_session_record(aoe_id, entry.uuid, model, effort):
        # Best-effort cleanup so we don't leave dangling graveyard records.
        _aoe(["aoe", "remove", "--profile", PROFILE, title])
        return _rollback("could not patch sessions.json with --resume args")

    start = _aoe(["aoe", "session", "start", "--profile", PROFILE, title])
    if start.returncode != 0:
        _aoe(["aoe", "remove", "--profile", PROFILE, title])
        return _rollback(f"aoe session start failed: {(start.stderr or start.stdout)[:200]}")

    # Wait for claude's prompt to render. PR #1's wait_for_pane_ready already
    # handles the placeholder ↔ ready transition; here we layer on the
    # specific resume-prompt handling for "Resume from summary / Resume full".
    if not bus.wait_for_pane_ready(aoe_id, timeout=PANE_READY_TIMEOUT_S):
        bus.audit("necromance.pane_not_ready", uuid=entry.uuid, aoe_id=aoe_id)
        _aoe(["aoe", "session", "stop", "--profile", PROFILE, title])
        _aoe(["aoe", "remove", "--profile", PROFILE, title])
        return _rollback("necromance pane did not become ready within timeout")

    if not _dismiss_resume_prompt(aoe_id, summary_mode=summary_mode):
        # Not fatal — newer claude versions may not show the prompt at all
        # (user dismissed it permanently with "Don't ask me again"). Just log.
        bus.audit("necromance.no_resume_prompt", uuid=entry.uuid, aoe_id=aoe_id)

    # Build the consultation peer-msg.
    thread_id = f"t_necro_{_short(entry.uuid)}_{int(time.time())}"
    body = _preamble(caller.label) + "\n\nQUESTION FROM " + caller.label + ":\n" + question
    msg = pm.PeerMsg(
        sender_label=caller.label,
        thread=thread_id,
        msg_type="ask",
        body=body,
        at=bus.now_iso(),
    )
    inbox.append_to(aoe_id, msg)

    ok, output = bus.aoe_send_peer_msg(
        aoe_id, caller.label, thread_id, msg.to_wire(),
        force_send=True,
    )
    if not ok:
        bus.audit("necromance.send_failed", uuid=entry.uuid, aoe_id=aoe_id, error=output[:200])
        # Don't tear down — body is in inbox.md; the resurrected agent will
        # see it on its next prompt-submit via the hook.

    now2 = time.time()
    necro = Necromancy(
        uuid=entry.uuid,
        label=entry.label,
        aoe_id=aoe_id,
        title=title,
        started_at=provisional.started_at,  # preserve original spawn time
        last_active=now2,
        thread_id=thread_id,
        status=STATUS_LIVE,
    )
    # Promote: launching → live (atomic via tmp+replace).
    necro.write_lock()
    bus.audit("necromance.raised",
              uuid=entry.uuid, label=entry.label, aoe_id=aoe_id,
              caller=caller.aoe_id, thread=thread_id, model=model, effort=effort,
              summary_mode=summary_mode, question_bytes=len(question))
    return True, (
        f"raised {entry.label} as {title} (claude {_short(entry.uuid)}); "
        f"reply will arrive on thread {thread_id}"
    ), necro


def _send_follow_up(
    caller: bus.SessionIdentity,
    necro: Necromancy,
    question: str,
) -> tuple[bool, str, Necromancy]:
    """Send a question to an already-resurrected session. New thread per question."""
    thread_id = f"t_necro_{_short(necro.uuid)}_{int(time.time())}"
    body = (
        f"🕯 Follow-up from {caller.label!r} (you are still in the consultation).\n\n"
        f"QUESTION: {question}"
    )
    msg = pm.PeerMsg(
        sender_label=caller.label,
        thread=thread_id,
        msg_type="ask",
        body=body,
        at=bus.now_iso(),
    )
    inbox.append_to(necro.aoe_id, msg)
    ok, _ = bus.aoe_send_peer_msg(
        necro.aoe_id, caller.label, thread_id, msg.to_wire(),
        force_send=True,
    )
    necro.thread_id = thread_id
    necro.touch()  # extend TTL
    bus.audit("necromance.follow_up",
              uuid=necro.uuid, aoe_id=necro.aoe_id, caller=caller.aoe_id,
              thread=thread_id, delivered=ok, question_bytes=len(question))
    suffix = "" if ok else " (queued in inbox.md; tmux send failed)"
    return True, f"follow-up sent on thread {thread_id}{suffix}", necro


# ---------- Resume-prompt handling ----------

def _dismiss_resume_prompt(aoe_id: str, *, summary_mode: bool = False) -> bool:
    """Send the right keystroke to dismiss claude's resume-mode prompt.

    Looks for the marker text within RESUME_PROMPT_TIMEOUT_S. If found, types
    the chosen option key + Enter. If not found within the timeout, assumes
    the prompt isn't being shown (e.g. user picked "Don't ask me again" in a
    previous session) and returns False without sending anything.

    After dismissing the prompt, we wait for the pane to render the actual
    claude input row again — the prompt-dismissal causes a redraw and we want
    to land our peer-msg into a fully-rendered input, not the mid-redraw state.
    """
    tmux_session = bus._find_tmux_session_for(aoe_id)
    if tmux_session is None:
        return False

    deadline = time.monotonic() + RESUME_PROMPT_TIMEOUT_S
    found = False
    while time.monotonic() < deadline:
        try:
            proc = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", tmux_session, "-S", "-30"],
                capture_output=True, text=True, timeout=3,
            )
            if proc.returncode == 0:
                stripped = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", proc.stdout)
                if _looks_like_resume_prompt(stripped):
                    found = True
                    break
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            pass
        time.sleep(0.3)

    if not found:
        return False

    key = RESUME_SUMMARY_KEY if summary_mode else RESUME_FULL_KEY
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", tmux_session, key, "Enter"],
            check=False, timeout=3,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False

    # Let claude render the actual conversation; another wait_for_pane_ready
    # will fire upstream before the consultation peer-msg is sent.
    bus.wait_for_pane_ready(aoe_id, timeout=PANE_READY_TIMEOUT_S)
    return True


# ---------- Resume-prompt detector (MED-1: tighter match) ----------

# Claude's resume picker renders exactly this shape (chrome flexes a bit
# between releases but always shows numbered options with `❯` selection):
#   ❯ 1. Resume from summary (recommended)
#     2. Resume full session as-is
#     3. Don't ask me again
# We match the OPTION line, not the bare marker string, so a transcript
# scroll-line that mentions "resume from summary" doesn't fire a false
# positive.
_RESUME_OPTION_RE = re.compile(
    r"^\s*[❯>]?\s*[123]\.\s+(Resume from summary|Resume full session|Don't ask me again)",
    re.MULTILINE,
)


def _looks_like_resume_prompt(captured: str) -> bool:
    """True only if the captured pane content contains a Claude-Code resume
    picker's numbered-option row, not just a substring mention."""
    return bool(_RESUME_OPTION_RE.search(captured))


# ---------- Live-session guard (MED-4) ----------

def _is_live_in_default_profile(uuid: str) -> bool:
    """True if the target claude UUID is currently the ``agent_session_id`` of
    a RUNNING session in the default AoE profile.

    Forking a live claude jsonl via ``--resume`` from another process yields
    undefined behaviour (parallel writers, divergent state). Refuse early.
    """
    if not bus.SESSIONS_JSON.exists():
        return False
    try:
        data = json.loads(bus.SESSIONS_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    sessions = data if isinstance(data, list) else data.get("sessions", [])
    for s in sessions:
        if s.get("agent_session_id") == uuid and s.get("status") == "running":
            return True
    return False


# ---------- Release ----------

def release(necro: Necromancy, reason: str = "ttl_expired") -> bool:
    """Stop and remove a necromanced session.

    MED-6: unlink the lock BEFORE the aoe-stop/remove calls. The unlink is
    the advisory "I own this release" claim — if a concurrent cull beats us
    to it, our unlink raises FileNotFoundError and we early-return, avoiding
    double aoe-stop noise and double audit lines.
    """
    try:
        necro.lock_path().unlink()
    except FileNotFoundError:
        # Another caller already released this necromancy. Idempotent no-op.
        return False
    ok_stop = _aoe([
        "aoe", "session", "stop", "--profile", PROFILE, necro.title,
    ], timeout=15.0).returncode == 0
    _aoe(["aoe", "remove", "--profile", PROFILE, necro.title], timeout=15.0)
    bus.audit("necromance.released",
              uuid=necro.uuid, aoe_id=necro.aoe_id, title=necro.title,
              reason=reason, ok_stop=ok_stop)
    return ok_stop


def cull(now: Optional[float] = None, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> list[str]:
    """Stop and remove every necromancy whose last_active is older than TTL,
    plus any provisional (status=launching) locks older than
    ``LAUNCH_TIMEOUT_SECONDS`` (orphaned by a crashed spawner).

    Returns the list of uuids released. Idempotent; safe to call repeatedly
    from cron.
    """
    now = now if now is not None else time.time()
    released = []
    for necro in list_live():
        age = now - necro.last_active
        if necro.status == STATUS_LAUNCHING:
            if age >= LAUNCH_TIMEOUT_SECONDS:
                if release(necro, reason="launch_orphaned"):
                    released.append(necro.uuid)
            continue
        if age >= ttl_seconds:
            if release(necro, reason="ttl_expired"):
                released.append(necro.uuid)
    return released


# ---------- tmux helpers ----------

def _tmux_alive(aoe_id: str) -> bool:
    """True if the AoE session is still in tmux's session list."""
    try:
        proc = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=3,
        )
        if proc.returncode != 0:
            return False
        prefix = aoe_id[:8]
        for sess in proc.stdout.splitlines():
            if sess.endswith(prefix) or f"_{prefix}" in sess:
                return True
        return False
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return False
