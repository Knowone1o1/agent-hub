"""The only way tokens are spent: child processes of the local, logged-in `claude` CLI (Claude Code).

No Anthropic SDK and no API key: every call runs `claude -p` on the user's Claude subscription.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # Windows: never pop up a console
TAG_STEP = re.compile(r"\[STEP:\s*([^\]\n]{1,200})\]")
TAG_PROGRESS = re.compile(r"\[PROGRESS:\s*(\d+)\s*/\s*(\d+)\s*(?:\|\s*([^\]\n]{0,120}))?\]")
TAG_RESULT = re.compile(r"\[RESULT:\s*([^\]]{1,600})\]")
TAG_NEW_TASK = re.compile(r"\[NEW_TASK:\s*([^\]@]{3,200}?)\s*(?:@([\w-]+))?\s*\]")

_version = {"checked": False, "ok": False, "version": None, "path": None, "error": None}


def check(force=False):
    """`claude --version`: is the subscription CLI available?"""
    if _version["checked"] and not force:
        return dict(_version)
    exe = shutil.which("claude")
    _version.update(checked=True, path=exe, ok=False, version=None, error=None)
    if not exe:
        _version["error"] = "The `claude` CLI was not found on PATH. Install Claude Code and run `claude` once to sign in."
        return dict(_version)
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8", timeout=30,
                           creationflags=NO_WINDOW)
        out = (r.stdout or r.stderr).strip()
        _version.update(ok=r.returncode == 0, version=out.splitlines()[0] if out else None,
                        error=None if r.returncode == 0 else out[-300:])
    except Exception as e:  # noqa: BLE001
        _version["error"] = f"{type(e).__name__}: {e}"
    return dict(_version)


class Run:
    """One streamed `claude -p` call. Callbacks receive live steps, progress, log lines and tool use."""

    def __init__(self, prompt, system, cwd, model="sonnet", tools=None, on_step=None, on_progress=None, on_log=None,
                 timeout=1800):
        self.prompt, self.system, self.cwd, self.model = prompt, system, Path(cwd), model
        self.tools = tools or []          # [] = no tools at all (pure thinking/writing)
        self.on_step, self.on_progress, self.on_log = on_step, on_progress, on_log
        self.timeout = timeout
        self.proc = None
        self.text = ""                    # all assistant text, streamed
        self.final = ""                   # the CLI's final result text
        self.cost_usd = 0.0
        self.usage = {}
        self.error = None
        self.limited = False
        self.stopped = False
        self._scan = 0

    def stop(self):
        self.stopped = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                pass

    def _emit_tags(self):
        chunk = self.text[self._scan:]
        last_nl = chunk.rfind("]")
        if last_nl < 0:
            return
        seg = chunk[:last_nl + 1]
        self._scan += last_nl + 1
        for m in TAG_STEP.finditer(seg):
            self.on_step and self.on_step(m.group(1).strip())
        for m in TAG_PROGRESS.finditer(seg):
            cur, tot = int(m.group(1)), max(1, int(m.group(2)))
            self.on_progress and self.on_progress(min(cur, tot), tot, (m.group(3) or "").strip())

    def _log(self, kind, text):
        if self.on_log and text:
            self.on_log(kind, text)

    def run(self):
        exe = shutil.which("claude")
        if not exe:
            self.error = "claude CLI not found"
            return self
        fd, sp = tempfile.mkstemp(suffix=".md", prefix="studio-sys-")
        os.close(fd)
        Path(sp).write_text(self.system, encoding="utf-8")
        args = [exe, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                "--model", self.model, "--append-system-prompt-file", sp, "--strict-mcp-config",
                "--disable-slash-commands", "--no-session-persistence"]
        if self.tools:
            args += ["--permission-mode", "acceptEdits", "--allowedTools", ",".join(self.tools)]
        else:
            args += ["--tools", ""]
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)   # make sure the subscription login is used, never a stray API key
        try:
            self.proc = subprocess.Popen(args, cwd=str(self.cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                         creationflags=NO_WINDOW, env=env)
            self.proc.stdin.write(self.prompt)
            self.proc.stdin.close()
            err_lines = []
            threading.Thread(target=lambda: err_lines.extend(self.proc.stderr), daemon=True).start()
            timer = threading.Timer(self.timeout, self.stop)
            timer.start()
            tool_inputs = {}
            for line in self.proc.stdout:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._handle(ev, tool_inputs)
            self.proc.wait()
            timer.cancel()
            self._emit_tags()
            if self.stopped and not self.error:
                self.error = "stopped"
            elif self.proc.returncode not in (0, None) and not self.final and not self.error:
                self.error = ("".join(err_lines) or f"claude exited with code {self.proc.returncode}").strip()[-600:]
            low = (self.error or "").lower() + self.final.lower()[:300]
            if any(k in low for k in ("usage limit", "rate limit", "limit reached", "out of extra usage")):
                self.limited = True
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
        finally:
            try:
                os.remove(sp)
            except OSError:
                pass
        return self

    def _handle(self, ev, tool_inputs):
        t = ev.get("type")
        if t == "stream_event":
            e = ev.get("event", {})
            if e.get("type") == "content_block_delta" and e.get("delta", {}).get("type") == "text_delta":
                self.text += e["delta"].get("text", "")
                self._emit_tags()
            elif e.get("type") == "content_block_start" and e.get("content_block", {}).get("type") == "tool_use":
                name = e["content_block"].get("name", "tool")
                self.on_step and self.on_step(f"Using {name}…", tool=True)
        elif t == "assistant":
            self.text += "\n"
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "text" and b.get("text", "").strip():
                    self._log("say", b["text"].strip())
                elif b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    target = inp.get("file_path") or inp.get("path") or inp.get("pattern") or inp.get("command") or ""
                    target = str(target)
                    root = str(self.cwd)
                    if target.lower().startswith(root.lower()):
                        target = target[len(root):].lstrip("\\/")
                    tool_inputs[b.get("id")] = target
                    self._log("tool", f"{b.get('name')} {target}".strip())
                    self.on_step and self.on_step(f"{b.get('name')} {target}".strip()[:120], tool=True)
        elif t == "user":
            for b in ev.get("message", {}).get("content", []) if isinstance(ev.get("message", {}).get("content"), list) else []:
                if b.get("type") == "tool_result":
                    c = b.get("content")
                    txt = c if isinstance(c, str) else " ".join(x.get("text", "") for x in (c or []) if isinstance(x, dict))
                    self._log("result", ("✗ " if b.get("is_error") else "✓ ") + txt.strip().replace("\n", " ")[:220])
        elif t == "system" and ev.get("subtype") == "task_summary" and ev.get("detail"):
            self._log("note", ev["detail"])
        elif t == "rate_limit_event":
            if (ev.get("rate_limit_info") or {}).get("status") not in (None, "allowed", "allowed_warning"):
                self.limited = True
        if "total_cost_usd" in ev or t == "result":
            self.cost_usd = float(ev.get("total_cost_usd") or 0)
            self.usage = ev.get("usage") or {}
            self.final = str(ev.get("result") or "")
            if ev.get("is_error"):
                self.error = self.final[-600:] or "error"


def parse_json_block(text):
    """The last JSON object in a reply (fenced or bare)."""
    for m in reversed(list(re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S))):
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    break
        start = text.find("{", start + 1)
    return None
