"""
Claude Code MCP Bridge
=======================
MCP server that wraps Claude Code CLI so Odysseus can use it as a tool.
Registers tools that forward prompts to `claude -p "..." --print`.

Usage:
  python claude_mcp_bridge.py          # Starts MCP server on stdio

Or test a prompt directly:
  python claude_mcp_bridge.py --test "Say hello"
"""

import json
import os
import subprocess
import sys
from pathlib import Path


CLAUDE_CMD = os.environ.get("CLAUDE_CMD", "claude")
MODEL = os.environ.get("CLAUDE_MODEL", "")


def call_claude(prompt: str, max_tokens: int = 4096) -> str:
    """Run a prompt through Claude Code CLI in print mode."""
    cmd = [CLAUDE_CMD, "-p", prompt, "--print"]
    if MODEL:
        cmd.extend(["--model", MODEL])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "CLAUDE_CODE_HEADLESS": "1"},
        )
        output = result.stdout.strip()
        if result.stderr:
            # Claude may print non-essential info to stderr
            pass
        return output or result.stderr[:2000] or "No output from Claude"
    except subprocess.TimeoutExpired:
        return "Error: Claude timed out after 300s"
    except FileNotFoundError:
        return f"Error: Claude CLI not found at '{CLAUDE_CMD}'"
    except Exception as e:
        return f"Error: {str(e)}"


# ── MCP Protocol Handler ──────────────────────────────────────────────────

SUPPORTED_TOOLS = {
    "ask_claude": {
        "name": "ask_claude",
        "description": "Ask Claude Code a question or give it a task. Use for complex reasoning, code generation, research, or any task that benefits from Claude's capabilities.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The prompt to send to Claude Code"
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Max tokens in response (default: 4096, max: 32768)",
                    "default": 4096
                }
            },
            "required": ["prompt"]
        }
    }
}


def handle_mcp_request(request: dict) -> dict:
    """Handle an MCP JSON-RPC request."""
    req_id = request.get("id")
    method = request.get("method", "")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "claude-code-bridge",
                    "version": "1.0.0"
                }
            }
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": list(SUPPORTED_TOOLS.values())
            }
        }

    elif method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "ask_claude":
            prompt = arguments.get("prompt", "")
            max_tokens = min(arguments.get("max_tokens", 4096), 32768)
            if not prompt:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": "prompt is required"}
                }
            result = call_claude(prompt, max_tokens)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result}]
                }
            }
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
            }

    elif method == "notifications/initialized":
        return None  # No response needed

    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"}
        }


def main():
    if "--test" in sys.argv:
        # Test mode: run a single prompt and print result
        idx = sys.argv.index("--test")
        prompt = " ".join(sys.argv[idx + 1:]) if len(sys.argv) > idx + 1 else "Say hello"
        print(f"Prompt: {prompt}")
        print(f"Response: {call_claude(prompt)}")
        return

    # MCP server mode: read JSON-RPC from stdin, write to stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_mcp_request(request)
            if response is not None:
                print(json.dumps(response), flush=True)
        except json.JSONDecodeError:
            continue


if __name__ == "__main__":
    main()
