"""Codex kept running in the background, so a rewrite no longer pays for starting the command-line tool each time.

`codex exec` needs about 9 seconds before the model even sees your text (loading its configuration, plugins and account).
`codex app-server` is the same engine that Codex's own editor extension talks to: it starts once, when Rover starts, and
each rewrite then only costs a new throw-away conversation (about 0.2 s) plus the model's answer. It uses your existing
Codex login and the model you chose, exactly like the command-line path, which stays as the fallback.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

from i18n import tr

enabled = True              # tests switch it off; the app falls back to `codex exec` on any trouble anyway
PRIORITY = "priority"       # the service tier the Codex apps call "Fast" (about 1.5x quicker, uses more credits)
INSTRUCTIONS = "You rewrite dictated text. Use no tools. Reply with the final text only."


class CodexServerError(RuntimeError):
    pass


class CodexServer:
    def __init__(self, command_factory=None):
        self._command_factory = command_factory
        self.process: subprocess.Popen | None = None
        self.signature = ""
        self.lock = threading.Lock()                         # one rewrite at a time
        self._start_lock = threading.Lock()
        self._ids = 0
        self._responses: dict[int, queue.Queue] = {}
        self._turns: dict[str, queue.Queue] = {}

    # -- process ----------------------------------------------------------------------------------------------
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, command: list[str], env: dict, cwd: Path, signature: str, timeout: float = 60.0) -> None:
        with self._start_lock:
            if self.running() and self.signature == signature:
                return
            self.stop()
            self.signature = signature
            try:
                self.process = subprocess.Popen(
                    command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                    errors="replace", env=env, cwd=str(cwd), bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as exc:
                raise CodexServerError(str(exc)) from exc
            try:
                import accel
                accel._kill_with_us(self.process)            # never outlive Rover, however it ends
            except Exception:
                pass
            process = self.process
            threading.Thread(target=self._read, args=(process,), daemon=True).start()
            try:
                self._call("initialize", {"clientInfo": {"name": "llm-mascot", "title": "LLM Mascot", "version": "1"}}, timeout)
                self._send({"method": "initialized", "params": {}})
            except CodexServerError:
                self.stop()
                raise

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass
        for waiting in list(self._responses.values()) + list(self._turns.values()):
            waiting.put(None)                                # wake anybody waiting on a dead process

    # -- protocol ---------------------------------------------------------------------------------------------
    def _send(self, message: dict) -> None:
        process = self.process
        if process is None or process.poll() is not None or process.stdin is None:
            raise CodexServerError(tr("Codex is not running"))
        try:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise CodexServerError(str(exc)) from exc

    def _call(self, method: str, params: dict, timeout: float) -> dict:
        self._ids += 1
        request_id = self._ids
        waiting: queue.Queue = queue.Queue()
        self._responses[request_id] = waiting
        try:
            self._send({"method": method, "id": request_id, "params": params})
            try:
                reply = waiting.get(timeout=timeout)
            except queue.Empty:
                raise CodexServerError(tr("Codex did not answer in time")) from None
        finally:
            self._responses.pop(request_id, None)
        if reply is None:
            raise CodexServerError(tr("Codex stopped unexpectedly"))
        if "error" in reply:
            raise CodexServerError(str(reply["error"].get("message", reply["error"]))[:200])
        return reply.get("result", {})

    def _read(self, process: subprocess.Popen) -> None:
        for line in process.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if "method" in message and "id" in message:       # the server asking us something (an approval): we never approve
                try:
                    self._send({"id": message["id"], "error": {"code": -32601, "message": "not supported"}})
                except CodexServerError:
                    pass
            elif "method" in message:
                thread = (message.get("params") or {}).get("threadId")
                if thread in self._turns and message["method"] == "turn/completed":
                    self._turns[thread].put(message["params"])
            elif message.get("id") in self._responses:
                self._responses[message["id"]].put(message)
        for waiting in list(self._responses.values()) + list(self._turns.values()):
            waiting.put(None)

    # -- one rewrite ------------------------------------------------------------------------------------------
    def rewrite(self, prompt: str, model: str = "", effort: str = "low", tier: str = "", cwd: Path | str = ".", timeout: float = 90.0) -> str:
        if not self.running():
            raise CodexServerError(tr("Codex is not running"))
        with self.lock:
            params: dict = {"ephemeral": True, "sandbox": "read-only", "approvalPolicy": "never", "cwd": str(cwd),
                            "baseInstructions": INSTRUCTIONS}
            if model:
                params["model"] = model
            if tier == PRIORITY:
                params["serviceTier"] = PRIORITY
            thread = self._call("thread/start", params, 30)["thread"]["id"]
            finished: queue.Queue = queue.Queue()
            self._turns[thread] = finished
            try:
                turn = {"threadId": thread, "input": [{"type": "text", "text": prompt}]}
                if effort in ("low", "medium", "high"):
                    turn["effort"] = effort
                self._call("turn/start", turn, 30)
                try:
                    done = finished.get(timeout=timeout)
                except queue.Empty:
                    raise CodexServerError(tr("Codex did not answer in time")) from None
                if done is None:
                    raise CodexServerError(tr("Codex stopped unexpectedly"))
            finally:
                self._turns.pop(thread, None)
                try:
                    self._send({"method": "thread/unsubscribe", "id": self._next_id(), "params": {"threadId": thread}})
                except CodexServerError:
                    pass
        turn_info = done.get("turn") or {}
        if turn_info.get("status") == "failed" or turn_info.get("error"):
            raise CodexServerError(str((turn_info.get("error") or {}).get("message", "failed"))[:200])
        texts = [item.get("text", "") for item in turn_info.get("items", []) if item.get("type") == "agentMessage"]
        text = (texts[-1] if texts else "").strip().strip('`"\n ')
        if not text:
            raise CodexServerError(tr("Codex returned an empty answer"))
        return text

    def _next_id(self) -> int:
        self._ids += 1
        return self._ids


server = CodexServer()
