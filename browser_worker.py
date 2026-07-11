#!/usr/bin/env python3
"""
Browser Worker — browser-use agent subprocess for Hermes browser_agent toolset.

Communicates with the tool handler via JSON-lines over stdio:
  Worker→tool: ready, status, progress, info_request, escalation_request,
               result, error, heartbeat
  Tool→worker: clarify_response, cancel

Connects to an already-running CloakBrowser instance via CDP
(managed by stealth-browser.service). Does NOT launch or manage the browser.
"""

import asyncio
import json
import logging
import os
import signal
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "logs" / "browser-agent"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("browser_worker")
logger.setLevel(logging.DEBUG)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CDP_URL = os.environ.get("BROWSER_CDP_URL", "http://127.0.0.1:9377")
LLM_BASE_URL = os.environ.get("BROWSER_LLM_BASE_URL", "http://192.168.68.55:8080/v1")
#LLM_BASE_URL = os.environ.get("BROWSER_LLM_BASE_URL", "http://192.168.68.62:8080/v1")
LLM_API_KEY = os.environ.get("BROWSER_LLM_API_KEY", "local")
DEFAULT_TIMEOUT = int(os.environ.get("BROWSER_TASK_TIMEOUT", "300"))
HEARTBEAT_INTERVAL = 10  # seconds


# ---------------------------------------------------------------------------
# CDP health check
# ---------------------------------------------------------------------------

def check_cdp_alive(cdp_url: str, timeout: int = 5) -> bool:
    """Verify the CloakBrowser CDP endpoint is reachable before starting."""
    try:
        import urllib.request
        req = urllib.request.Request(f"{cdp_url}/json/version", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return bool(data.get("Browser"))
    except Exception:
        return False

# ---------------------------------------------------------------------------
# JSON-lines protocol helpers
# ---------------------------------------------------------------------------

def emit(evt: Dict[str, Any]) -> None:
    """Write a JSON-lines event to stdout for the tool handler."""
    line = json.dumps(evt, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def emit_status(message: str, **extra) -> None:
    emit({"type": "status", "message": message, **extra})


def emit_progress(step: int, max_steps: int) -> None:
    pct = int(step / max(max_steps, 1) * 100)
    emit({"type": "progress", "step": step, "max_steps": max_steps, "percent": pct})


def emit_result(ok: bool, summary: str, data: Optional[Dict] = None,
                screenshot_path: Optional[str] = None) -> None:
    evt: Dict[str, Any] = {"type": "result", "ok": ok, "summary": summary}
    if data:
        evt["data"] = data
    if screenshot_path:
        evt["screenshot_path"] = screenshot_path
    emit(evt)


def emit_error(message: str, tb: Optional[str] = None) -> None:
    evt: Dict[str, Any] = {"type": "error", "message": message}
    if tb:
        evt["traceback"] = tb
    emit(evt)


# ---------------------------------------------------------------------------
# Stdin reader thread — reads clarify_response / cancel from tool handler
# ---------------------------------------------------------------------------
class StdinReader:
    """Background thread reading JSON-lines from stdin (tool→worker)."""

    def __init__(self):
        self._pending_clarify: Dict[str, threading.Event] = {}
        self._answers: Dict[str, str] = {}
        self._cancelled = threading.Event()
        self._lock = threading.Lock()

    def register_clarify(self, request_id: str, event: threading.Event) -> None:
        with self._lock:
            self._pending_clarify[request_id] = event

    def get_answer(self, request_id: str) -> Optional[str]:
        with self._lock:
            return self._answers.pop(request_id, None)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def run(self) -> None:
        """Block-reading loop — runs in a daemon thread."""
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    cmd = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cmd_type = cmd.get("type")
                if cmd_type == "clarify_response":
                    req_id = cmd.get("request_id", "")
                    answer = cmd.get("answer", "")
                    with self._lock:
                        self._answers[req_id] = answer
                        evt = self._pending_clarify.pop(req_id, None)
                    if evt:
                        evt.set()
                elif cmd_type == "cancel":
                    self._cancelled.set()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# LLM auto-detection
# ---------------------------------------------------------------------------

def detect_model_id() -> str:
    """Query the LLM endpoint for the first available model id."""
    try:
        import urllib.request
        req = urllib.request.Request(f"{LLM_BASE_URL}/models")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = data.get("data", [])
            if models:
                return models[0].get("id", "local-model")
    except Exception:
        pass
    return "local-model"


# ---------------------------------------------------------------------------
# Main worker logic
# ---------------------------------------------------------------------------

async def run_browser_agent(goal: str, constraints: str, timeout: int,
                            stdin_reader: StdinReader,
                            task_id: str,
                            explicit_model: Optional[str] = None) -> None:
    """Run the browser-use agent against the CloakBrowser CDP endpoint."""
    from browser_use import Agent, BrowserSession
    from browser_use.llm.openai.chat import ChatOpenAI
    from openai import AsyncOpenAI

    class CustomChatOpenAI(ChatOpenAI):
        def get_client(self) -> AsyncOpenAI:
            client = super().get_client()
            original_create = client.chat.completions.create
            async def custom_create(*args, **kwargs):
                kwargs["extra_body"] = kwargs.get("extra_body", {})
                kwargs["extra_body"]["reasoning"] = "off"
                return await original_create(*args, **kwargs)
            client.chat.completions.create = custom_create
            return client

    model_id = explicit_model or detect_model_id()
    emit_status(f"Using LLM model: {model_id} at {LLM_BASE_URL}")

    # LLM setup — browser-use's native ChatOpenAI (compatible with llama.cpp / vLLM)
    llm = CustomChatOpenAI(
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        model=model_id,
    )

    from browser_use.controller import Controller

    # Connect to already-running CloakBrowser via CDP
    session = BrowserSession(
        cdp_url=CDP_URL,
        is_local=False,
    )

    # Build task prompt with optional constraints
    task = goal
    if constraints:
        task = f"{goal}\n\nConstraints:\n{constraints}"
        
    task += "\n\nCRITICAL RULE: NEVER fabricate or guess personal details, email addresses, phone numbers, or any other specific information to fill forms. If you lack information, you MUST use the `ask_human` tool to request the necessary details from the user."
    task += "\n\nCRITICAL RULE (UK ADDRESSES): When filling out UK addresses, ALWAYS input the postcode first into the address search field to trigger the address lookup dropdown. Do NOT enter the full address directly into the search field; wait for the dropdown and pick the correct address."

    step_count = 0
    MAX_STEPS = 100

    def on_step(browser_state, agent_output, n_steps) -> None:
        nonlocal step_count
        step_count = n_steps
        emit_progress(step_count, max_steps=MAX_STEPS)
        # Emit status from the agent's current action
        try:
            if agent_output and hasattr(agent_output, 'current_state'):
                action_str = str(agent_output.current_state)[:120]
                emit_status(f"Step {n_steps}: {action_str}")
        except Exception:
            emit_status(f"Step {n_steps} in progress")

    async def should_stop() -> bool:
        return stdin_reader.cancelled

    controller = Controller()

    @controller.action("Ask the human a question if you lack information to proceed. Do not fabricate or guess details.")
    def ask_human(question: str) -> str:
        req_id = uuid4().hex[:8]
        evt = threading.Event()
        stdin_reader.register_clarify(req_id, evt)
        emit({"type": "info_request", "question": question, "request_id": req_id})
        # Wait for the response
        while not evt.wait(timeout=1.0):
            if stdin_reader.cancelled:
                return "Task was cancelled by user."
        return stdin_reader.get_answer(req_id) or "User provided no answer."

    import subprocess
    import re
    import os

    def start_cloudflared_tunnel(port: int) -> tuple[Optional[subprocess.Popen], str]:
        """Start cloudflared tunnel and return (process, url)."""
        try:
            proc = subprocess.Popen(
                ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            os.set_blocking(proc.stderr.fileno(), False)
            url = ""
            start_time = time.time()
            while time.time() - start_time < 8.0:
                line = proc.stderr.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                if match:
                    url = match.group(0) + "/vnc.html"
                    break
            
            if url:
                return proc, url
                
            proc.terminate()
            return None, ""
        except Exception as e:
            logger.error(f"Failed to start cloudflared: {e}")
            return None, ""

    @controller.action("Escalate to human if you are stuck in a loop, blocked by a CAPTCHA, or cannot proceed. The human will connect via VNC to help.")
    def escalate_to_human(reason: str) -> str:
        req_id = uuid4().hex[:8]
        evt = threading.Event()
        stdin_reader.register_clarify(req_id, evt)
        
        tunnel_proc, tunnel_url = start_cloudflared_tunnel(6080)
        
        emit({
            "type": "escalation_request",
            "reason": reason,
            "tunnel_url": tunnel_url or "http://localhost:6080/vnc.html",
            "request_id": req_id
        })
        # Wait for human to complete their intervention
        while not evt.wait(timeout=1.0):
            if stdin_reader.cancelled:
                if tunnel_proc:
                    tunnel_proc.terminate()
                return "Task was cancelled by user."
                
        if tunnel_proc:
            tunnel_proc.terminate()
        return stdin_reader.get_answer(req_id) or "Human intervened successfully. You may proceed."

    agent = Agent(
        task=task,
        llm=llm,
        browser_session=session,
        controller=controller,
        register_new_step_callback=on_step,
        register_should_stop_callback=should_stop,
        enable_signal_handler=False,
        step_timeout=600,
        llm_timeout=600,
    )

    emit_status("Starting browser-use agent")

    try:
        # Run the agent — it has its own internal timeout/step limits
        # We use a soft timeout here that's generous to account for cleanup
        soft_timeout = timeout + 60  # Add 60s buffer for browser cleanup
        result = await asyncio.wait_for(
            agent.run(max_steps=MAX_STEPS),
            timeout=soft_timeout,
        )
        # Extract clean summary from AgentHistoryList
        summary = result.final_result() if result else None
        
        ok = True
        if result:
            if not result.is_done():
                ok = False
                summary = summary or "Task failed or was stopped before completion (e.g. stuck in loop)."
            if result.has_errors():
                # If there are errors in the last step, maybe it failed
                errors = result.errors()
                if errors and errors[-1]:
                    summary = summary or errors[-1]
                    ok = False

        if not summary:
            summary = "Task completed"

        # Capture final screenshot
        screenshot_path = None
        try:
            page = await session.get_current_page()
            if page:
                screenshot_path = str(LOG_DIR / f"{task_id}_final.png")
                b64_data = await page.screenshot()
                import base64
                with open(screenshot_path, "wb") as f:
                    f.write(base64.b64decode(b64_data))
        except Exception:
            pass

        emit_result(ok=ok, summary=summary, screenshot_path=screenshot_path)
    except asyncio.TimeoutError:
        emit_result(ok=False, summary=f"Task timed out after {soft_timeout}s (original timeout: {timeout}s)")
    except Exception as exc:
        # Capture screenshot on failure/cancellation too
        screenshot_path = None
        try:
            if session:
                page = await session.get_current_page()
                if page:
                    screenshot_path = str(LOG_DIR / f"{task_id}_final.png")
                    b64_data = await page.screenshot()
                    import base64
                    with open(screenshot_path, "wb") as f:
                        f.write(base64.b64decode(b64_data))
        except Exception:
            pass
        emit_error(str(exc), traceback.format_exc())
    finally:
        try:
            await session.close()
        except Exception:
            pass


def heartbeat_loop(reader: StdinReader) -> None:
    """Emit heartbeat events until cancelled."""
    while not reader.cancelled:
        emit({"type": "heartbeat"})
        time.sleep(HEARTBEAT_INTERVAL)


def main() -> None:
    # Parse task spec from argv
    if len(sys.argv) < 2:
        emit_error("Usage: browser_worker.py '<json_task_spec>'")
        sys.exit(1)

    try:
        spec = json.loads(sys.argv[1])
    except json.JSONDecodeError as exc:
        emit_error(f"Invalid JSON task spec: {exc}")
        sys.exit(1)

    goal = spec.get("goal", "")
    constraints = spec.get("constraints", "")
    timeout = int(spec.get("timeout", DEFAULT_TIMEOUT))

    # LLM config — task_spec overrides env vars (lets Hermes pass its own endpoint)
    global LLM_BASE_URL, LLM_API_KEY
    if spec.get("llm_base_url"):
        LLM_BASE_URL = spec["llm_base_url"]
    if spec.get("llm_api_key"):
        LLM_API_KEY = spec["llm_api_key"]
    _llm_model = spec.get("llm_model")  # optional explicit model id

    if not goal:
        emit_error("Task spec missing 'goal'")
        sys.exit(1)

    # Setup file logging
    task_id = spec.get("task_id", "unknown")
    log_file = LOG_DIR / f"{task_id}.log"
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)

    # CDP health check — fail fast if CloakBrowser is down
    if not check_cdp_alive(CDP_URL):
        emit_error(
            f"CloakBrowser CDP endpoint not reachable at {CDP_URL}. "
            f"Ensure stealth-browser.service is running: systemctl --user status stealth-browser.service"
        )
        sys.exit(1)

    emit({"type": "ready", "task_id": task_id})
    logger.info(f"Worker started: goal={goal!r}, timeout={timeout}, cdp={CDP_URL}, llm={LLM_BASE_URL}")

    # Start stdin reader thread
    stdin_reader = StdinReader()
    t = threading.Thread(target=stdin_reader.run, daemon=True)
    t.start()

    # Start heartbeat thread
    hb = threading.Thread(target=heartbeat_loop, args=(stdin_reader,), daemon=True)
    hb.start()

    # Run the async browser agent
    try:
        asyncio.run(run_browser_agent(goal, constraints, timeout, stdin_reader, task_id, _llm_model))
    except KeyboardInterrupt:
        emit_result(ok=False, summary="Interrupted by user")
    except Exception as exc:
        emit_error(str(exc), traceback.format_exc())
    finally:
        logger.info("Worker exiting")


if __name__ == "__main__":
    main()
