"""Agent Studio HTTP server (FastAPI). Live updates go to the UI over Server-Sent Events (/api/events)."""
import asyncio
import threading
import time

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import PUBLIC_DIR, agents, bus, claude_cli, principal, projects, runner, uploads

app = FastAPI(title="Agent Studio", docs_url=None, redoc_url=None)


def _err(e):
    code = 404 if isinstance(e, KeyError) else 400
    raise HTTPException(code, str(e).strip("'\""))


# ---------------------------------------------------------------- overview

@app.get("/api/health")
def health():
    return {"cli": claude_cli.check(), "mode": "Claude subscription via the local claude CLI (no API key)"}


@app.post("/api/health/recheck")
def recheck():
    return {"cli": claude_cli.check(force=True)}


def agent_view(a, taken=None, names=None):
    taken = projects.assignments() if taken is None else taken
    slug = taken.get(a["id"])
    return {**a, "project": slug, "project_name": (names or {}).get(slug, slug), "live": runner.snap(a["id"])}


@app.get("/api/state")
def state():
    taken = projects.assignments()
    projs = projects.all_projects()
    names = {p["slug"]: p["name"] for p in projs}
    return {"agents": [agent_view(a, taken, names) for a in agents.all_agents()],
            "projects": [{**p, "sprint": runner.sprint_state(p["slug"])} for p in projs],
            "settings": runner.settings(), "totals": runner.TOTALS, "cli": claude_cli.check()}


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    return runner.save_settings(body)


# ---------------------------------------------------------------- projects

@app.get("/api/projects/{slug}")
def project(slug: str):
    try:
        return {"project": {**projects.get(slug), "sprint": runner.sprint_state(slug)},
                "tasks": projects.read_tasks(slug), "changelog": projects.changelog(slug, 80),
                "context": projects.context_text(slug)}
    except KeyError as e:
        _err(e)


@app.post("/api/projects/propose")
def propose(body: dict = Body(...)):
    name, brief = (body.get("name") or "").strip(), (body.get("brief") or "").strip()
    if not name or len(brief) < 10:
        raise HTTPException(400, "Give the project a name and a brief of at least a sentence.")
    try:
        return principal.propose(name, brief, body.get("slug"), uploads.summary_for_principal(body.get("upload")))
    except (KeyError, ValueError) as e:
        _err(e)


@app.post("/api/projects")
def assemble(body: dict = Body(...)):
    try:
        meta = principal.assemble(body.get("name", "").strip() or "Untitled", body.get("brief", ""),
                                  body.get("summary", ""), body.get("draft", []), body.get("recruit", []),
                                  body.get("tasks", []), body.get("slug"))
        if body.get("upload"):
            uploads.attach_to_project(body["upload"], meta["slug"])
            bus.publish({"type": "board", "slug": meta["slug"]})
        return {"project": projects.get(meta["slug"])}
    except (KeyError, ValueError) as e:
        _err(e)


# ---------------------------------------------------------------- brief attachments (staged until the team is assembled)

@app.post("/api/uploads")
def upload(body: dict = Body(...)):
    try:
        token, files = uploads.add(body.get("token"), body.get("files", []))
        return {"token": token, "files": files}
    except (ValueError, OSError) as e:
        _err(ValueError(str(e)))


@app.delete("/api/uploads/{token}/{name}")
def upload_remove(token: str, name: str):
    try:
        return {"files": uploads.remove(token, name)}
    except ValueError as e:
        _err(e)


@app.post("/api/projects/{slug}/files")
def project_files(slug: str, body: dict = Body(...)):
    """Add files to an existing project (dropped on the Project memory view)."""
    try:
        token, _ = uploads.add(None, body.get("files", []))
        moved = uploads.attach_to_project(token, slug)
        bus.publish({"type": "board", "slug": slug})
        return {"files": moved}
    except (KeyError, ValueError, OSError) as e:
        _err(e if isinstance(e, KeyError) else ValueError(str(e)))


@app.post("/api/projects/{slug}/tasks")
def add_task(slug: str, body: dict = Body(...)):
    try:
        tid = projects.add_task(slug, body.get("title", ""), body.get("assignee") or None, body.get("column", "Up Next"))
        projects.log_change(slug, "you", f"added {tid}: {body.get('title', '')}")
        bus.publish({"type": "board", "slug": slug})
        return {"id": tid}
    except KeyError as e:
        _err(e)


@app.patch("/api/projects/{slug}/tasks/{tid}")
def patch_task(slug: str, tid: str, body: dict = Body(...)):
    try:
        t = projects.update_task(slug, tid, **body)
        bus.publish({"type": "board", "slug": slug})
        return {"task": t}
    except KeyError as e:
        _err(e)


@app.delete("/api/projects/{slug}/tasks/{tid}")
def del_task(slug: str, tid: str):
    projects.delete_task(slug, tid)
    bus.publish({"type": "board", "slug": slug})
    return {"ok": True}


@app.post("/api/projects/{slug}/sprint")
def sprint(slug: str, body: dict = Body(default={})):
    try:
        return {"sprint": runner.start_sprint(slug, bool(body.get("autopilot")))}
    except (KeyError, ValueError) as e:
        _err(e)


@app.post("/api/projects/{slug}/stop")
def stop(slug: str):
    try:
        runner.stop_sprint(slug)
        return {"ok": True}
    except KeyError as e:
        _err(e)


# ---------------------------------------------------------------- agents

@app.get("/api/agents/{aid}")
def agent(aid: str):
    try:
        projs = {p["slug"]: p["name"] for p in projects.all_projects()}
        return {"agent": agent_view(agents.get(aid), names=projs), "log": runner.logs(aid)}
    except KeyError as e:
        _err(e)


@app.patch("/api/agents/{aid}")
def patch_agent(aid: str, body: dict = Body(...)):
    try:
        a = agents.update(aid, body)
        bus.publish({"type": "roster"})
        return {"agent": a}
    except KeyError as e:
        _err(e)


@app.post("/api/agents/{aid}/assign")
def assign(aid: str, body: dict = Body(...)):
    slug = body.get("slug")
    try:
        a = agents.get(aid)
        if a["kind"] == "principal":
            raise ValueError("The Principal oversees every project and isn't assigned to one.")
        where = projects.assignments().get(aid)
        if where and where != slug:
            raise ValueError(f"{a['name']} is on “{projects.get(where)['name']}”. Release them first (one project per agent).")
        meta = projects.get(slug)
        if aid not in meta["team"]:
            meta["team"].append(aid)
            projects.save(meta)
            projects.log_change(slug, "you", f"added @{aid} to the team")
        bus.publish({"type": "roster"})
        return {"ok": True}
    except (KeyError, ValueError) as e:
        _err(e)


@app.post("/api/agents/{aid}/release")
def release(aid: str):
    where = projects.assignments().get(aid)
    if not where:
        return {"ok": True}
    if runner.is_busy(aid):
        raise HTTPException(400, "This agent is working right now. Stop it first.")
    meta = projects.get(where)
    meta["team"] = [a for a in meta["team"] if a != aid]
    projects.save(meta)
    projects.log_change(where, "you", f"moved @{aid} to the Sideline")
    bus.publish({"type": "roster"})
    return {"ok": True}


@app.post("/api/agents/{aid}/prompt")
def prompt_agent(aid: str, body: dict = Body(...)):
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Write a message first.")
    try:
        a = agents.get(aid)
    except KeyError as e:
        _err(e)
    if runner.is_busy(aid):
        raise HTTPException(400, f"{a['name']} is busy. Wait for the current turn or stop it.")
    slug = projects.assignments().get(aid)

    def go():
        if slug:
            system = f"{runner.persona(a)}\n\n{runner.onboarding(slug)}\n\n{runner.PROTOCOL}"
            run = runner.run_agent(aid, f"Message from the user:\n{text}", system, projects.pdir(slug), slug=slug,
                                   label="Answering you")
            res = claude_cli.TAG_RESULT.findall(run.text + run.final)
            c = projects.log_change(slug, aid, f"(direct request) {res[-1] if res else text[:120]}")
            bus.publish({"type": "changelog", "slug": slug, "entry": c})
        else:   # on the Sideline: talk only, no tools, no project
            system = runner.persona(a) + "\n\nYou are on the Sideline (not on a project). Chat with the user; print [STEP: ...] tags while you think."
            from . import STATE_DIR
            runner.run_agent(aid, text, system, STATE_DIR, label="Answering you", use_tools=False)

    threading.Thread(target=go, daemon=True).start()
    return {"ok": True}


@app.post("/api/agents/{aid}/stop")
def stop_agent(aid: str):
    runner.stop_agent(aid)
    return {"ok": True}


@app.delete("/api/agents/{aid}")
def delete_agent(aid: str, body: dict = Body(default={})):
    """USER-ONLY deletion. The UI sends {"confirm": "<agent id>"} after the user confirms; nothing else calls this."""
    if body.get("confirm") != aid:
        raise HTTPException(400, "Deletion needs confirmation from the user.")
    if runner.is_busy(aid):
        raise HTTPException(400, "This agent is working. Stop it before deleting.")
    try:
        where = projects.assignments().get(aid)
        if where:
            meta = projects.get(where)
            meta["team"] = [a for a in meta["team"] if a != aid]
            projects.save(meta)
            projects.log_change(where, "you", f"deleted agent @{aid}")
        agents.delete_by_user(aid)
        bus.publish({"type": "roster"})
        return {"ok": True}
    except (KeyError, ValueError) as e:
        _err(e)


# ---------------------------------------------------------------- live events (SSE)

@app.get("/api/events")
async def events():
    q = bus.subscribe()

    async def stream():
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _watch_status_files():
    """External edits to STATUS.md (or CHANGELOG.log) refresh the board live."""
    seen = {}
    while True:
        for p in projects.all_projects():
            try:
                c = projects.cache(p["slug"])
                key = tuple((c / f).stat().st_mtime for f in ("STATUS.md", "CHANGELOG.log"))
            except (OSError, KeyError):
                continue
            if seen.get(p["slug"]) not in (None, key):
                bus.publish({"type": "board", "slug": p["slug"]})
            seen[p["slug"]] = key
        time.sleep(1.0)


@app.on_event("startup")
def _startup():
    claude_cli.check(force=True)
    uploads.cleanup()
    threading.Thread(target=_watch_status_files, daemon=True).start()


@app.post("/api/shutdown")
def shutdown():
    import os
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return {"ok": True}


@app.get("/")
def index():
    """Serve the page with version stamps on its scripts/styles, so the desktop window never runs stale cached files."""
    from fastapi.responses import HTMLResponse
    html = (PUBLIC_DIR / "index.html").read_text(encoding="utf-8")
    for f in ("styles.css", "app.js", "avatar.js"):
        try:
            v = int((PUBLIC_DIR / f).stat().st_mtime)
        except OSError:
            v = 0
        html = html.replace(f'/static/{f}"', f'/static/{f}?v={v}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=str(PUBLIC_DIR)), name="static")
