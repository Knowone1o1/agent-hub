"""The Principal: reads a brief and the roster, proposes a team, and plans sprints.

It can draft, recruit (create new profiles once the user approves) and move agents to the Sideline.
It can never delete an agent: there is no code path from here to agents.delete_by_user().
"""
from . import STATE_DIR, agents, bus, projects, runner
from .claude_cli import parse_json_block


def _brief_roster(exclude_slug=None):
    taken = projects.assignments()
    names = {p["slug"]: p["name"] for p in projects.all_projects()}
    avail, locked = [], []
    for a in agents.all_agents():
        if a["kind"] == "principal":
            continue
        line = (f"- id `{a['id']}`: {a['name']} {a['avatar']}, {a['role']} (model {a['model']}). "
                f"Skills: {', '.join(a['skills']) or '-'}. Bio: {a['bio'][:260].replace(chr(10), ' ')}")
        where = taken.get(a["id"])
        if where and where != exclude_slug:
            locked.append(line + f"  [LOCKED to project “{names.get(where, where)}”]")
        else:
            avail.append(line + ("  [already on this project]" if where == exclude_slug and where else ""))
    return avail, locked


def _principal_run(prompt, slug=None, label="Thinking…"):
    p = agents.principal()
    if not p:
        raise ValueError("No Principal profile found in .agents/ (kind: principal).")
    if runner.is_busy(p["id"]):
        raise ValueError("The Principal is busy with another request. Try again in a moment.")
    system = (f"{runner.persona(p)}\n\nYou are the Principal: you staff and steer projects; you do not write project "
              "code yourself. While you think, print [STEP: ...] and [PROGRESS: n/total | label] tags on their own "
              "lines. Your final answer must end with one JSON object in a ```json fence, exactly as requested.")
    cwd = projects.pdir(slug) if slug else STATE_DIR
    run = runner.run_agent(p["id"], prompt, system, cwd, slug=slug, label=label, use_tools=False)
    data = parse_json_block(run.final or run.text)
    if data is None:
        raise ValueError(f"The Principal's reply could not be read{': ' + run.error if run.error else ''}.")
    return data, run


def propose(name, brief, slug=None, files_note=""):
    """Brief -> staffing plan: draft existing agents, recruit new specialists, first tasks."""
    avail, locked = _brief_roster(exclude_slug=slug)
    mode = "re-staffing the existing project" if slug else "a new project"
    current = ""
    if slug:
        current = f"\nCurrent team on this project:\n{runner.team_lines(slug)}\nCurrent board:\n{projects.status_text(slug)}\n"
    prompt = f"""The user is starting {mode}.

PROJECT NAME: {name}
BRIEF (the user's words):
{brief}
{current}
{('ATTACHED FILES (the user dropped these in; they will be saved in the project folder under inputs/, readable by every agent):' + chr(10) + files_note + chr(10) + 'Take them into account: they may contain requirements, data, designs or existing code. Where useful, make a first task about reading or analysing them.' + chr(10)) if files_note else ''}
AVAILABLE AGENTS (may be drafted):
{chr(10).join(avail) or '- none'}

LOCKED AGENTS (on another project; one project per agent, do NOT draft them):
{chr(10).join(locked) or '- none'}

Do this:
1. Analyse the brief: the workload, what can run in parallel, and the skills it needs.
2. Draft the available agents that fit (with a one-sentence reason each). Don't draft agents that add nothing.
3. If skills are missing, or extra bandwidth would save real time, recruit new specialists (at most 4). Give each a
   distinct name, a fitting single emoji avatar, a hex colour, a personality, a writing tone, skills, a model
   (opus for hard reasoning, sonnet by default, haiku for light/fast work) and why they are needed.
   Also give each a distinctive cartoon "look" so they stand out from the existing cast. Pick from:
   hat none|beret|cap|headphones|beanie|bow|hardhat|helmet|explorer|deerstalker|crown|wig;
   outfit hoodie|jacket|turtleneck|vest|labcoat|smock|blazer|sweater; hair short|spiky|bob|bun|long|curly|ponytail|buzz|mohawk;
   hairColor (hex); glasses none|round|square|monocle; extra none|freckles|blush|mustache|beard; eyes round|happy|wide|lashes.
   Typical recruits: Researcher, Judge / Quality Critic, Parallel Coder #2, Security Auditor, Data Architect.
4. Write a short plan summary (approach + tech stack) and 3-8 first tasks, each with an assignee: an agent id from
   your draft, or the exact name of one of your recruits.

Reply with your reasoning briefly, then end with this JSON in a ```json fence:
{{"summary": "2-4 sentences: approach and stack",
  "draft": [{{"id": "<agent id>", "reason": "..."}}],
  "recruit": [{{"name": "...", "role": "...", "avatar": "🧪", "color": "#22d3ee", "model": "sonnet",
               "personality": "...", "tone": "...", "skills": ["..."], "why": "...",
               "look": "hat: deerstalker, outfit: jacket, hair: curly, hairColor: #a0522d, glasses: round"}}],
  "tasks": [{{"title": "...", "assignee": "<agent id or recruit name>"}}]}}"""
    data, run = _principal_run(prompt, slug, label=f"Reviewing the roster for “{name}”")
    ok_ids = {a["id"] for a in projects.available_agents(exclude_slug=slug)}
    draft = [d for d in data.get("draft", []) if isinstance(d, dict) and d.get("id") in ok_ids]
    recruit = [r for r in data.get("recruit", []) if isinstance(r, dict) and r.get("name")][:4]
    tasks = [t for t in data.get("tasks", []) if isinstance(t, dict) and t.get("title")][:12]
    return {"summary": str(data.get("summary", ""))[:1500], "draft": draft, "recruit": recruit, "tasks": tasks,
            "cost_usd": run.cost_usd}


def assemble(name, brief, summary, draft_ids, recruits, tasks, slug=None):
    """The user approved: write recruit profiles, assign the team, initialise or extend the project cache."""
    new_ids = {}
    for r in recruits:
        a = agents.create(r, created_by="principal (approved by user)")
        new_ids[r.get("name", "").strip().lower()] = a["id"]
    team = list(dict.fromkeys(list(draft_ids) + list(new_ids.values())))

    def who(x):
        x = (x or "").strip()
        return new_ids.get(x.lower(), x) if x else None

    task_specs = [{"title": t["title"], "assignee": who(t.get("assignee")) if who(t.get("assignee")) in team else None}
                  for t in tasks if t.get("title")]
    if slug:
        meta = projects.get(slug)
        taken = projects.assignments()
        clash = [a for a in team if taken.get(a, slug) != slug]
        if clash:
            raise ValueError(f"Already on another project: {', '.join(clash)}")
        meta["team"] = list(dict.fromkeys(meta.get("team", []) + team))
        projects.save(meta)
        ctx = projects.cache(slug) / "CONTEXT.md"
        with open(ctx, "a", encoding="utf-8") as f:
            f.write(f"\n\n## Re-staffing brief\n{brief.strip()}\n\n**Principal's plan:** {summary.strip()}\n")
        for t in task_specs:
            projects.add_task(slug, t["title"], t["assignee"])
        projects.log_change(slug, "principal", f"Re-staffed; team is now {', '.join(meta['team'])}")
    else:
        meta = projects.create(name, brief, team, task_specs, summary)
        slug = meta["slug"]
    bus.publish({"type": "roster"})
    bus.publish({"type": "board", "slug": slug})
    return projects.get(slug)


def plan_sprint(slug):
    """Assign Up Next tasks to idle team members; may add tasks and bench agents (never delete)."""
    meta = projects.get(slug)
    team = [a for a in meta.get("team", []) if not runner.is_busy(a)]
    if not meta.get("team"):
        return {"assign": [], "note": "No team on this project yet."}
    log = "\n".join(f"{c['t']} {c['who']}: {c['text']}" for c in projects.changelog(slug, 25))
    taken = projects.assignments()
    sideline = [a for a in agents.all_agents() if a["kind"] != "principal" and a["id"] not in taken]
    side_lines = "\n".join(f"- @{a['id']}: {a['name']} {a['avatar']}, {a['role']}" for a in sideline) or "- nobody"
    prompt = f"""Plan the next sprint for project “{meta['name']}”.

TEAM (idle and available now):
{chr(10).join(l for l in runner.team_lines(slug).splitlines() if any(f'@{a}:' in l for a in team)) or '- nobody idle'}

ON THE SIDELINE (free, not on any project; you may recall them onto this team):
{side_lines}

BOARD (.project-cache/STATUS.md):
{projects.status_text(slug)}

RECENT CHANGELOG:
{log or '(empty)'}

Rules:
- Give each idle agent at most ONE task from "Up Next" that fits their role; parallelise independent work.
- You may add new tasks if the project needs them (e.g. a review by a Judge after code lands).
  To assign a new task in the same sprint, use its exact title as the "task" value.
- If an agent's phase is finished or they are not needed for now, you may move them to the Sideline (bench).
  Benching frees them for other projects. You can never delete an agent.
- If a task needs someone on the Sideline (e.g. the Judge once code exists), recall them; you can assign them
  a task in the same sprint.
- If the project is complete and verified, assign nothing and say so in "note".

End with this JSON in a ```json fence:
{{"note": "one sentence for the user about this sprint",
  "recall": [{{"agent": "<agent id from the Sideline>", "reason": "..."}}],
  "new_tasks": [{{"title": "...", "assignee": "<agent id or null>"}}],
  "assign": [{{"agent": "<agent id>", "task": "<task id like T3, or the exact title of a new task>"}}],
  "bench": [{{"agent": "<agent id>", "reason": "..."}}]}}"""
    data, _ = _principal_run(prompt, slug, label="Planning the sprint")
    free = {a["id"] for a in sideline} - set(projects.assignments())   # re-check: someone may have taken them meanwhile
    recalled = []
    for rc in data.get("recall", []):
        aid = rc.get("agent") if isinstance(rc, dict) else None
        if aid in free and aid not in recalled:
            recalled.append(aid)
            projects.log_change(slug, "principal", f"recalled @{aid} from the Sideline: {rc.get('reason', '')}")
    if recalled:
        meta = projects.get(slug)
        meta["team"] = list(dict.fromkeys(meta["team"] + recalled))
        projects.save(meta)
        team = team + recalled
        bus.publish({"type": "roster"})
    title_to_id = {}
    for nt in data.get("new_tasks", [])[:8]:
        if isinstance(nt, dict) and nt.get("title"):
            tid = projects.add_task(slug, nt["title"], nt.get("assignee") if nt.get("assignee") in meta["team"] else None)
            if tid:
                title_to_id[nt["title"].strip().lower()] = tid
                projects.log_change(slug, "principal", f"added {tid}: {nt['title']}")
    up_ids = {t["id"] for t in projects.read_tasks(slug) if t["column"] == "Up Next"}
    assign, used_agents, used_tasks = [], set(), set()
    for x in data.get("assign", []):
        if not isinstance(x, dict):
            continue
        aid, task = x.get("agent"), str(x.get("task", "")).strip()
        tid = task.upper() if task.upper() in up_ids else title_to_id.get(task.lower())
        if aid in team and tid in up_ids and aid not in used_agents and tid not in used_tasks:
            assign.append({"agent": aid, "task": tid})
            used_agents.add(aid)
            used_tasks.add(tid)
    benched = []
    for b in data.get("bench", []):
        aid = b.get("agent") if isinstance(b, dict) else None
        if aid in meta["team"] and aid not in used_agents and not runner.is_busy(aid):
            benched.append(aid)
            projects.log_change(slug, "principal", f"moved @{aid} to the Sideline: {b.get('reason', '')}")
    if benched:
        meta = projects.get(slug)
        meta["team"] = [a for a in meta["team"] if a not in benched]
        projects.save(meta)
        bus.publish({"type": "roster"})
    note = str(data.get("note", ""))[:300]
    if note:
        projects.log_change(slug, "principal", note)
    bus.publish({"type": "board", "slug": slug})
    return {"assign": assign, "note": note, "benched": benched}
