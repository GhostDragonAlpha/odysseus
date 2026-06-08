"""
DYAD Mailbox System
=====================
Bidirectional file-based message queue between Odysseus and Claude Code.
Both sides can post requests and read responses.

The mailbox is a JSON file at the Chimera project root.
- Claude Code reads requests, writes responses
- Odysseus reads responses, can write requests

Usage from Claude Code:
  python dyad_mailbox.py send "Analyze this" --files GameMode.h
  python dyad_mailbox.py read

Usage from Python:
  from dyad_mailbox import DyadMailbox
  mb = DyadMailbox()
  mb.send("task", {"prompt": "..."})
  result = mb.receive("task")
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

MAILBOX_PATH = Path(os.environ.get("DYAD_MAILBOX", "E:\\Chimera\\DYAD_MAILBOX.json"))


@dataclass
class DyadMessage:
    """A single message in the DYAD mailbox."""
    id: str
    kind: str               # "request", "response", "task", "result"
    sender: str             # "claude", "odysseus", "unreal", "user"
    recipient: str          # "claude", "odysseus", "unreal", "user"
    prompt: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    status: str = "pending"  # "pending", "processing", "done", "failed"
    created_at: str = ""
    completed_at: str = ""
    result: str = ""
    error: str = ""
    session_id: str = ""


class DyadMailbox:
    """File-based bidirectional message queue."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else MAILBOX_PATH
        self._ensure()

    def _ensure(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({
                "version": 3,
                "status": "idle",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "messages": [],
                "active_message": None,
            })

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return {"version": 3, "status": "idle", "messages": [], "active_message": None}

    def _write(self, data: dict):
        self.path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def send(self, kind: str, recipient: str, prompt: str = "",
             payload: dict | None = None, files: list[str] | None = None) -> str:
        """Send a message to the mailbox."""
        data = self._read()
        msg_id = str(uuid.uuid4())[:12]
        msg = DyadMessage(
            id=msg_id,
            kind=kind,
            sender="odysseus",
            recipient=recipient,
            prompt=prompt,
            payload=payload or {},
            files=files or [],
            status="pending",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        data.setdefault("messages", []).append(asdict(msg))
        data["active_message"] = msg_id
        data["status"] = "pending"
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write(data)
        return msg_id

    def receive(self, msg_id: str | None = None) -> DyadMessage | None:
        """Read a message by ID, or the active one."""
        data = self._read()
        target = msg_id or data.get("active_message")
        if not target:
            return None
        for m in data.get("messages", []):
            if m["id"] == target:
                return DyadMessage(**m)
        return None

    def list_pending(self, recipient: str = "claude") -> list[DyadMessage]:
        """List all pending messages for a recipient."""
        data = self._read()
        return [
            DyadMessage(**m) for m in data.get("messages", [])
            if m.get("status") == "pending" and m.get("recipient") == recipient
        ]

    def mark_done(self, msg_id: str, result: str = "", error: str = ""):
        """Mark a message as done."""
        data = self._read()
        for m in data.get("messages", []):
            if m["id"] == msg_id:
                m["status"] = "failed" if error else "done"
                m["result"] = result
                m["error"] = error
                m["completed_at"] = datetime.now(timezone.utc).isoformat()
                break
        data["status"] = "idle"
        data["active_message"] = None
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write(data)

    def get_active(self) -> DyadMessage | None:
        """Get the currently active message (being processed)."""
        return self.receive()

    def wait_for_result(self, msg_id: str, timeout: float = 300,
                        poll_interval: float = 1.0) -> DyadMessage | None:
        """Wait for a message to be completed (polling)."""
        import time as _time
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            msg = self.receive(msg_id)
            if msg and msg.status in ("done", "failed"):
                return msg
            _time.sleep(poll_interval)
        return None

    def get_all(self, limit: int = 50) -> list[DyadMessage]:
        """Get all messages, newest first."""
        data = self._read()
        msgs = [DyadMessage(**m) for m in data.get("messages", [])]
        msgs.sort(key=lambda m: m.created_at, reverse=True)
        return msgs[:limit]


# ── Agent tool function ──

async def do_dyad_send(content: str, owner: Optional[str] = None) -> dict:
    """Send a message via the DYAD mailbox.
    JSON args: {prompt, recipient?, files?, kind?}
    """
    from src.tool_implementations import _parse_tool_args

    try:
        args = _parse_tool_args(content) if content.strip().startswith("{") else {}
    except ValueError:
        args = {}
    if not isinstance(args, dict):
        args = {}

    prompt = args.get("prompt", "")
    if not prompt:
        return {"error": "prompt is required", "exit_code": 1}

    mb = DyadMailbox()
    msg_id = mb.send(
        kind=args.get("kind", "task"),
        recipient=args.get("recipient", "claude"),
        prompt=prompt,
        payload=args.get("payload", {}),
        files=args.get("files", []),
    )

    return {
        "output": f"Message sent to DYAD mailbox (id={msg_id}, recipient={args.get('recipient', 'claude')})",
        "message_id": msg_id,
        "exit_code": 0,
    }


async def do_dyad_receive(content: str, owner: Optional[str] = None) -> dict:
    """Read a DYAD mailbox message.
    JSON args: {message_id?}
    """
    from src.tool_implementations import _parse_tool_args

    try:
        args = _parse_tool_args(content) if content.strip().startswith("{") else {}
    except ValueError:
        args = {}
    if not isinstance(args, dict):
        args = {}

    mb = DyadMailbox()
    msg_id = args.get("message_id", "")
    if msg_id:
        msg = mb.receive(msg_id)
    else:
        # Show pending messages
        pending = mb.list_pending(recipient="claude")
        recent = mb.get_all(limit=10)

        lines = []
        if pending:
            lines.append(f"Pending messages ({len(pending)}):")
            for m in pending:
                lines.append(f"  [{m.id}] {m.kind} from {m.sender}: {m.prompt[:80]}")
        lines.append(f"")
        lines.append(f"Recent messages ({len(recent)}):")
        for m in recent:
            status = "✅" if m.status == "done" else "❌" if m.status == "failed" else "⏳"
            lines.append(f"  {status} [{m.id}] {m.sender}->{m.recipient}: {m.prompt[:60]}")
        return {"output": "\n".join(lines), "exit_code": 0}

    if not msg:
        return {"error": f"Message '{msg_id}' not found", "exit_code": 1}

    info = (
        f"DYAD Message: {msg.id}\n"
        f"From: {msg.sender}  To: {msg.recipient}\n"
        f"Kind: {msg.kind}  Status: {msg.status}\n"
        f"Prompt: {msg.prompt}\n"
        f"Created: {msg.created_at}\n"
    )
    if msg.result:
        info += f"\nResult: {msg.result[:5000]}\n"
    if msg.error:
        info += f"\nError: {msg.error}\n"
    if msg.files:
        info += f"\nFiles: {', '.join(msg.files)}\n"

    return {"output": info, "exit_code": 0}
