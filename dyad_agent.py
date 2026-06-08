"""
DYAD Agent — Persistent Mailbox Watcher
=========================================
Runs in a visible cmd window, watches the DYAD mailbox at Chimera root
for new tasks from Odysseus. When a task appears, processes it
with Claude Code and writes the result back.

Usage:
  python dyad_agent.py                    # Watch mode (default)
  python dyad_agent.py --once             # Process one task, exit

The window shows:
  [DYAD Agent] Watching mailbox...
  [DYAD Agent] Task received: Fix orbital bug
  [DYAD Agent] Claude processing... (this may take a while)
  [DYAD Agent] Complete! Result written back.
  [DYAD Agent] Watching mailbox...
"""

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

MAILBOX_PATH = Path(os.environ.get("DYAD_MAILBOX", "E:/Chimera/DYAD_MAILBOX.json"))
CLAUDE_EXE = os.environ.get(
    "CLAUDE_CMD",
    "C:/Users/allen/node-portable/node-v20.17.0-win-x64/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
)
POLL_INTERVAL = 3  # seconds between mailbox checks


def log(msg: str):
    """Print a timestamped log message visible in the window."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def read_mailbox() -> dict:
    """Read the DYAD mailbox JSON."""
    try:
        return json.loads(MAILBOX_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": 3, "status": "idle", "messages": [], "active_message": None}


def write_mailbox(data: dict):
    """Write the DYAD mailbox JSON."""
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    MAILBOX_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def find_pending_tasks(data: dict) -> list[dict]:
    """Find all pending messages addressed to claude."""
    return [
        m for m in data.get("messages", [])
        if m.get("status") == "pending" and m.get("recipient") == "claude"
    ]


def process_task(msg: dict) -> bool:
    """Process a single task with Claude Code."""
    msg_id = msg.get("id", "unknown")
    prompt = msg.get("prompt", "")
    files = msg.get("files", [])

    if not prompt:
        log(f"[{msg_id}] ERROR: No prompt in task")
        return False

    # Build the full prompt with file contents
    full_prompt = prompt
    if files:
        full_prompt += "\n\n## Referenced Files\n"
        for fp in files[:10]:
            p = Path(fp)
            if p.exists():
                lang_map = {".py": "python", ".cpp": "cpp", ".h": "cpp",
                            ".cs": "csharp", ".js": "javascript", ".ts": "typescript",
                            ".json": "json", ".md": "markdown"}
                lang = lang_map.get(p.suffix.lower(), "")
                full_prompt += f"\n--- {fp} ---\n```{lang}\n"
                try:
                    full_prompt += p.read_text(encoding="utf-8", errors="replace")[:8000]
                except Exception as e:
                    full_prompt += f"[Error reading: {e}]"
                full_prompt += "\n```\n"
            else:
                full_prompt += f"\n[File not found: {fp}]\n"

    log(f"[{msg_id}] Processing: {prompt[:80]}...")

    # Mark as processing in mailbox
    data = read_mailbox()
    for m in data.get("messages", []):
        if m["id"] == msg_id:
            m["status"] = "processing"
    write_mailbox(data)

    # Run Claude Code
    try:
        log(f"[{msg_id}] Claude running...")
        start = time.time()

        result = subprocess.run(
            [CLAUDE_EXE, "-p", full_prompt, "--print"],
            capture_output=True, text=True, timeout=300,
            env={**os.environ, "CLAUDE_CODE_HEADLESS": "1"},
        )

        elapsed = time.time() - start
        output = result.stdout.strip() or result.stderr.strip() or "[No output]"
        log(f"[{msg_id}] Claude finished in {elapsed:.0f}s (exit={result.returncode})")

        # Update mailbox with result
        data = read_mailbox()
        for m in data.get("messages", []):
            if m["id"] == msg_id:
                m["status"] = "failed" if result.returncode != 0 else "done"
                m["result"] = output[:50000]
                m["error"] = result.stderr.strip() if result.returncode != 0 else ""
                m["completed_at"] = datetime.now(timezone.utc).isoformat()
        data["active_message"] = None
        # Only set idle if no other active messages
        has_remaining = bool(find_pending_tasks(data))
        data["status"] = "idle" if not has_remaining else "pending"
        write_mailbox(data)

        log(f"[{msg_id}] Result written to mailbox ({len(output)} chars)")
        return True

    except subprocess.TimeoutExpired:
        log(f"[{msg_id}] TIMEOUT after 300s")
        data = read_mailbox()
        for m in data.get("messages", []):
            if m["id"] == msg_id:
                m["status"] = "failed"
                m["error"] = "Timeout after 300s"
                m["completed_at"] = datetime.now(timezone.utc).isoformat()
        write_mailbox(data)
        return False

    except Exception as e:
        log(f"[{msg_id}] ERROR: {e}")
        traceback.print_exc()
        data = read_mailbox()
        for m in data.get("messages", []):
            if m["id"] == msg_id:
                m["status"] = "failed"
                m["error"] = str(e)
                m["completed_at"] = datetime.now(timezone.utc).isoformat()
        write_mailbox(data)
        return False


def watch_loop():
    """Main loop: poll mailbox, process tasks, sleep."""
    log("DYAD Agent started")
    log(f"Mailbox: {MAILBOX_PATH}")
    log(f"Claude: {CLAUDE_EXE}")
    log(f"Poll interval: {POLL_INTERVAL}s")
    log("Waiting for tasks from Odysseus...")
    print("-" * 60)

    while True:
        try:
            data = read_mailbox()
            pending = find_pending_tasks(data)

            if pending:
                for task in pending:
                    log(f"Task received: {task['prompt'][:80]}...")
                    process_task(task)
                # After processing, recheck immediately for new tasks
                continue

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            log("Shutdown requested. Exiting.")
            break
        except Exception as e:
            log(f"Unexpected error: {e}")
            traceback.print_exc()
            time.sleep(POLL_INTERVAL)


def main():
    print("=" * 60)
    print("  DYAD AGENT — Claude Code Mailbox Watcher")
    print("  Watches for tasks from Odysseus and processes them")
    print("=" * 60)
    print()

    if "--once" in sys.argv:
        data = read_mailbox()
        pending = find_pending_tasks(data)
        if not pending:
            print("No pending tasks.")
            return
        print(f"Processing {len(pending)} task(s)...")
        for task in pending:
            process_task(task)
        return

    watch_loop()


if __name__ == "__main__":
    main()
