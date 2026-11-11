"""
Agent Lab: networks of agents that communicate along drawn connections.

Storage (all plain files under data/lab/):
  networks/<id>.json   canvas layout, roles, connections, mode, protocol, caps
  memory/<id>.json     what each agent remembers between runs ("Reset agents" clears it)
  runs/<id>/run.json   run summary (live-updated while running)
  runs/<id>/trace.jsonl  every event: rounds, model calls with usage, messages, final answer

Execution modes
  rounds    each round every agent with something new acts once (in parallel), sending only along its
            outgoing connections; ends on a final answer, a cap, a stall, or a stop request.
  pipeline  the prompt flows through the connections once; each agent runs when all inputs are in.
"""

import cases
import scoring
import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LAB = ROOT / "data" / "lab"
NETS, MEM, RUNS = LAB / "networks", LAB / "memory", LAB / "runs"
for d in (NETS, MEM, RUNS):
    d.mkdir(parents=True, exist_ok=True)

LOCK = threading.RLock()
STOP = set()          # run ids asked to stop
ACTIVE = set()        # run ids currently executing
LIVE = {}             # run id -> Run object while executing (status is served from memory, not disk)
SCORE_VERSION = "v1"

# ------------------------------------------------------------------ role packs (fixed roles)

ROLE_PACKS = {
    "software": {
        "name": "Software incidents",
        "roles": [
            {"key": "coordinator", "name": "Coordinator", "coordinator": True, "color": "#7d9cff",
             "job": "Schedules the investigation, routes and reconciles findings, and submits the final diagnosis and repair plan.",
             "instructions": "You lead an incident-diagnosis team. Direct the specialists to the right evidence, reconcile conflicting findings, "
                             "and decide the root cause. Only submit a final answer when the evidence supports it; cite evidence IDs."},
            {"key": "app_logs", "name": "AppLogs", "color": "#e0a458",
             "job": "Investigates application logs: errors, exceptions, request traces.",
             "instructions": "You are the application-logs specialist. Report concrete findings from application logs with evidence IDs. "
                             "Say what you saw, what it implies, and how confident you are."},
            {"key": "database", "name": "Database", "color": "#3fb27f",
             "job": "Investigates database metrics: latency, locks, connections, replication.",
             "instructions": "You are the database specialist. Report concrete findings from database metrics with evidence IDs."},
            {"key": "deploy", "name": "Deploy", "color": "#d97757",
             "job": "Investigates deployments, releases, feature flags and configuration changes.",
             "instructions": "You are the deployment/configuration specialist. Report concrete findings about recent changes with evidence IDs."},
            {"key": "infra", "name": "Infra", "color": "#a78bfa",
             "job": "Investigates infrastructure and network telemetry: hosts, load balancers, DNS, packet loss.",
             "instructions": "You are the infrastructure/network specialist. Report concrete findings from telemetry with evidence IDs."},
        ],
        "v1_paths": [["deploy", "app_logs", ["release", "config"]], ["database", "app_logs", ["db", "latency"]],
                     ["infra", "database", ["network", "host"]]],
        "example_prompt": "Checkout error rate jumped from 0.2% to 9% at 14:05. Find the root cause and a fix.",
    },
    "supply": {
        "name": "Supply chain",
        "roles": [
            {"key": "coordinator", "name": "Coordinator", "coordinator": True, "color": "#7d9cff",
             "job": "Coordinates the team, reconciles findings, and submits the final diagnosis and action plan.",
             "instructions": "You lead a supply-chain disruption team (demand, inventory, logistics, supplier). Direct each specialist to "
                             "what matters, connect findings across domains (a stock-out is often a demand, supply and transport story "
                             "at once), challenge weak or conflicting claims, and decide the root cause. Your final answer names the root "
                             "cause, cites the evidence IDs that prove it, and gives a concrete action plan with owners and timing "
                             "(e.g. expedite, reallocate stock, adjust safety stock, switch carrier or supplier). Do not finalize on a guess."},
            {"key": "demand", "name": "Demand", "color": "#e0a458",
             "job": "Demand signals: forecasts vs actuals, orders, promotions, seasonality, customer mix.",
             "instructions": "You are the demand-planning specialist. Compare forecast with actual demand, spot spikes, promotions, "
                             "seasonality, new or lost customers and order-pattern changes (bullwhip, panic buying, batching). "
                             "Quantify: units, %, dates, SKUs, regions. Say whether demand explains the problem or rules it out."},
            {"key": "inventory", "name": "Inventory", "color": "#3fb27f",
             "job": "Stock positions: on-hand, in-transit, safety stock, reorder points, warehouses, stock-outs.",
             "instructions": "You are the inventory specialist. Report stock on hand and in transit per SKU and location, days of cover, "
                             "safety-stock and reorder-point settings, allocation rules and stock-out dates. Flag mismatches between "
                             "system stock and physical stock, and where stock could be reallocated."},
            {"key": "logistics", "name": "Logistics", "color": "#d97757",
             "job": "Transport and lead times: carriers, ports, customs, routes, ETAs and delays.",
             "instructions": "You are the logistics specialist. Report shipments, routes, carriers, ports, customs status, planned vs "
                             "actual lead times and ETAs, and their causes (congestion, capacity, weather, documentation). "
                             "Quantify delays in days and name the affected orders and SKUs. Suggest expedite or re-routing options with cost."},
            {"key": "supplier", "name": "Supplier", "color": "#a78bfa",
             "job": "Supplier side: capacity, confirmations, quality holds, MOQs, contracts and supplier risk.",
             "instructions": "You are the supplier specialist. Report order confirmations vs requests, supplier capacity and allocation, "
                             "production or quality issues, MOQs, contract terms and alternative or second-source options. "
                             "Quantify shortfalls (units, dates) and say how reliable each supplier signal is."},
        ],
        # Suggested V1 bypass paths: where a finding is usually needed directly, without the coordinator in between.
        "v1_paths": [["demand", "inventory", ["forecast", "demand", "orders"]],
                     ["supplier", "inventory", ["supply", "shortfall", "confirmation"]],
                     ["supplier", "logistics", ["shipment", "dispatch", "lead time"]],
                     ["logistics", "inventory", ["arrival", "delay", "eta"]],
                     ["inventory", "demand", ["stock", "availability"]]],
        "example_prompt": "Customer orders for SKU A-200 are being cut short in the EU since Monday. Find the root cause and an action plan.",
    },
    "general": {
        "name": "General problem solving",
        "roles": [
            {"key": "coordinator", "name": "Coordinator", "coordinator": True, "color": "#7d9cff",
             "job": "Breaks the task down, assigns work, combines results and submits the final answer.",
             "instructions": "You lead the team. Split the task, assign it, combine the results critically, and submit the final answer."},
            {"key": "researcher", "name": "Researcher", "color": "#e0a458",
             "job": "Gathers facts and options and compares approaches.",
             "instructions": "You are the researcher. Gather relevant facts and options; separate knowledge from assumption."},
            {"key": "analyst", "name": "Analyst", "color": "#3fb27f",
             "job": "Works through numbers, trade-offs and risks.",
             "instructions": "You are the analyst. Quantify where possible, state assumptions, and compare trade-offs."},
            {"key": "builder", "name": "Builder", "color": "#d97757",
             "job": "Produces the concrete deliverable: plan, text or code.",
             "instructions": "You are the builder. Produce the concrete deliverable, complete and ready to use."},
            {"key": "critic", "name": "Critic", "color": "#a78bfa",
             "job": "Finds weaknesses, errors and gaps in the team's work.",
             "instructions": "You are the critic. Find concrete weaknesses, rank them, and say when work is good enough."},
        ],
        "v1_paths": [["researcher", "analyst", []], ["analyst", "builder", []], ["builder", "critic", []]],
        "example_prompt": "Plan a 3-day product launch for a small online shop.",
    },
}

MODELS = [("sonnet", "Sonnet"), ("haiku", "Haiku"), ("opus", "Opus")]
PROTOCOLS = {
    "free": "Free: agents message along the arrows as they see fit.",
    "D0": "D0 coordinator-led: specialists talk only to the coordinator, which routes, reconciles and decides.",
    "V1": "V1 relay bypass: like D0, but evidence-linked findings travel directly along specialist-to-specialist arrows.",
    "V2": "V2 pull information: like D0, but findings arrive as short notices; full detail is requested by handle.",
    "V3": "V3 early escalation: like D0, plus contradiction alerts that pause only the affected agent until verified.",
    "A1": "A1 inhibitory evidence routing (AI research): like D0, but the router forwards the original cited evidence instead of the sender's conclusions, once per receiver per round, and cancels copies receivers already hold.",
    "A2": "A2 PACT action-state messages (AI research): like D0, but every message is a compact {action, state, result} record; reasoning and restatements are stripped.",
    "B1": "B1 adaptive hierarchy (business research): the team switches between flat rounds (anyone may talk to anyone, no decisions) and centralized rounds (positions go to the coordinator, who synthesises and decides).",
    "B2": "B2 facilitated information sharing (business research): like D0, plus a facilitator that nudges the specialist who has shared least of its private evidence, and a pre-decision check on unshared or unused evidence.",
}
# Where each method comes from (shown in the app and the docs).
PROTOCOL_SOURCES = {
    "D0": {"family": "baseline", "name": "Coordinator-led (default)", "source": "Pilot proposal, condition D0"},
    "V1": {"family": "proposal variant", "name": "Relay bypass", "source": "Pilot proposal, condition V1"},
    "V2": {"family": "proposal variant", "name": "Pull information", "source": "Pilot proposal, condition V2"},
    "V3": {"family": "proposal variant", "name": "Early escalation", "source": "Pilot proposal, condition V3"},
    "A1": {"family": "AI research", "name": "Inhibitory evidence routing",
           "source": "Siboni, Springer & Rostami (2026). Neuro-Inspired Inhibitory Message Passing for Accurate and Token-Efficient Multi-Agent Reasoning. arXiv:2610.24718",
           "url": "https://arxiv.org/abs/2610.24718"},
    "A2": {"family": "AI research", "name": "PACT action-state messages",
           "source": "Huang, Wu & Zhang (2026). What Should Agents Say? Action-state Communication for Efficient Multi-Agent Systems. arXiv:2606.05304",
           "url": "https://arxiv.org/abs/2606.05304"},
    "B1": {"family": "business research", "name": "Adaptive hierarchy",
           "source": "Abi-Esber, Greer & De Hoogh (2026). Team Hierarchical Adaptability: Benefits for Team Coordination and Performance. Academy of Management Journal 69(3), 563-592. doi:10.5465/amj.2023.1308",
           "url": "https://doi.org/10.5465/amj.2023.1308"},
    "B2": {"family": "business research", "name": "Facilitated information sharing",
           "source": "Alsobay, Rothschild, Hofman & Goldstein (2026). Bringing Everyone to the Table: An Experimental Study of LLM-Facilitated Group Decision Making. ACM CSCW 2026, arXiv:2508.08242",
           "url": "https://arxiv.org/abs/2508.08242"},
}
DEFAULT_CAPS = {"max_rounds": 8, "max_cost_usd": 2.0, "max_seconds": 900, "concurrency": 3,
                "alert_timeout_rounds": 3, "max_alerts": 3}
MSG_KINDS = ("finding", "request", "reply", "correction", "decision")


def role_def(pack, key):
    return next((r for r in ROLE_PACKS.get(pack, {}).get("roles", []) if r["key"] == key), None)


# ------------------------------------------------------------------ helpers

def _read(p, default=None):
    for attempt in range(20):
        try:
            return json.loads(Path(p).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return default
        except PermissionError:  # file is being replaced right now (Windows)
            time.sleep(0.02 * (attempt + 1))
    return default


def _write(p, obj):
    """Atomic write. On Windows, replacing a file fails while a reader has it open, so retry briefly."""
    p = Path(p)
    tmp = p.with_name(f"{p.name}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    for attempt in range(40):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(0.025 * (attempt + 1))
    tmp.unlink(missing_ok=True)
    raise PermissionError(f"could not write {p.name}")


def _safe_id(s):
    if not re.fullmatch(r"[a-z0-9-]{4,40}", s or ""):
        raise KeyError(s)
    return s


def new_id():
    return uuid.uuid4().hex[:10]


# ------------------------------------------------------------------ networks

def default_network(pack="supply"):
    roles = ROLE_PACKS[pack]["roles"]
    pos = [(460, 260), (160, 90), (760, 90), (160, 430), (760, 430)]
    nodes = [{"id": f"n{i + 1}", "role": r["key"], "name": r["name"], "x": pos[i][0], "y": pos[i][1]}
             for i, r in enumerate(roles)]
    edges = []
    for n in nodes[1:]:
        edges += [{"from": n["id"], "to": "n1"}, {"from": "n1", "to": n["id"]}]
    return {"id": new_id(), "name": f"{ROLE_PACKS[pack]['name']} team", "pack": pack, "mode": "rounds",
            "protocol": "free", "model": "sonnet", "persist_memory": False, "caps": dict(DEFAULT_CAPS),
            "nodes": nodes, "edges": edges, "created": time.time(), "updated": time.time()}


def apply_protocol_layout(n, protocol):
    """Rewire the arrows for a protocol: coordinator <-> every specialist; for V1 also the pack's suggested bypass paths.
    Agent positions and names are untouched."""
    n = dict(n)
    n["protocol"] = protocol if protocol in PROTOCOLS else n.get("protocol", "free")
    if n["protocol"] == "free":
        return n
    n["mode"] = "rounds"
    coords = [x for x in n["nodes"] if (role_def(n["pack"], x["role"]) or {}).get("coordinator")]
    if len(coords) != 1:
        raise ValueError(f"Protocol {n['protocol']} needs exactly one coordinator on the canvas (found {len(coords)}).")
    c = coords[0]["id"]
    edges = []
    for x in n["nodes"]:
        if x["id"] != c:
            edges += [{"from": x["id"], "to": c}, {"from": c, "to": x["id"]}]
    if n["protocol"] == "B1":  # flat rounds need every specialist able to reach every other specialist
        specs = [x["id"] for x in n["nodes"] if x["id"] != c]
        edges += [{"from": a, "to": b} for a in specs for b in specs if a != b]
    if n["protocol"] == "V1":
        for src_role, dst_role, topics in ROLE_PACKS[n["pack"]].get("v1_paths", []):
            for a in (x for x in n["nodes"] if x["role"] == src_role):
                for b in (x for x in n["nodes"] if x["role"] == dst_role):
                    edges.append({"from": a["id"], "to": b["id"], **({"topics": topics} if topics else {})})
    n["edges"] = edges
    return n


def list_networks():
    nets = [_read(f) for f in NETS.glob("*.json")]
    nets = [n for n in nets if n]
    seeded = LAB / ".seeded_supply"
    if not seeded.exists():  # supply chain is the starting scenario: create its team once
        n = apply_protocol_layout(default_network("supply"), "D0")
        n["name"] = "Supply chain team"
        _write(NETS / f"{n['id']}.json", validate_network(n))
        seeded.write_text("1", encoding="utf-8")
        nets.append(n)
    if not nets:
        n = default_network("supply")
        _write(NETS / f"{n['id']}.json", n)
        nets = [n]
    return sorted(nets, key=lambda n: (n.get("pack") != "supply", -n.get("updated", 0)))


def get_network(nid):
    n = _read(NETS / f"{_safe_id(nid)}.json")
    if not n:
        raise KeyError(nid)
    return n


def validate_network(n):
    pack = n.get("pack")
    if pack not in ROLE_PACKS:
        raise ValueError("Unknown role pack.")
    names, ids = set(), set()
    for node in n["nodes"]:
        r = role_def(pack, node.get("role"))
        if not r:
            raise ValueError(f"Role '{node.get('role')}' is not part of the {ROLE_PACKS[pack]['name']} pack.")
        node["name"] = re.sub(r"[^\w-]", "", node.get("name") or r["name"])[:24] or r["name"]
        if node["name"].lower() in names:
            raise ValueError(f"Two agents are called {node['name']}; names must be unique.")
        names.add(node["name"].lower())
        ids.add(node["id"])
    n["edges"] = [e for e in n["edges"] if e["from"] in ids and e["to"] in ids and e["from"] != e["to"]]
    seen, edges = set(), []
    for e in n["edges"]:
        k = (e["from"], e["to"])
        if k not in seen:
            seen.add(k)
            topics = [str(t).strip().lower()[:40] for t in (e.get("topics") or []) if str(t).strip()][:10]
            edges.append({"from": e["from"], "to": e["to"], **({"topics": topics} if topics else {})})
    n["edges"] = edges
    if n.get("mode") not in ("rounds", "pipeline"):
        n["mode"] = "rounds"
    if n.get("protocol") not in PROTOCOLS:
        n["protocol"] = "free"
    if n.get("model") not in [m for m, _ in MODELS]:
        n["model"] = "sonnet"
    caps = dict(DEFAULT_CAPS)
    caps.update({k: v for k, v in (n.get("caps") or {}).items() if k in DEFAULT_CAPS})
    caps["max_rounds"] = max(1, min(int(caps["max_rounds"]), 40))
    caps["max_cost_usd"] = max(0.05, min(float(caps["max_cost_usd"]), 50))
    caps["max_seconds"] = max(30, min(int(caps["max_seconds"]), 7200))
    caps["concurrency"] = max(1, min(int(caps["concurrency"]), 6))
    caps["alert_timeout_rounds"] = max(1, min(int(caps["alert_timeout_rounds"]), 10))
    caps["max_alerts"] = max(1, min(int(caps["max_alerts"]), 20))
    n["caps"] = caps
    return n


def protocol_problems(n):
    """Reasons a network can't run under its protocol (empty list = fine). D0-V3 are coordinator-led."""
    if n.get("protocol", "free") == "free":
        return []
    if n.get("mode") == "pipeline":
        return ["Protocols D0-V3 need Rounds mode."]
    coords = [x for x in n["nodes"] if (role_def(n["pack"], x["role"]) or {}).get("coordinator")]
    if len(coords) != 1:
        return [f"Protocol {n['protocol']} needs exactly one coordinator (found {len(coords)})."]
    c = coords[0]["id"]
    edges = {(e["from"], e["to"]) for e in n["edges"]}
    probs = []
    for x in n["nodes"]:
        if x["id"] == c:
            continue
        if (x["id"], c) not in edges or (c, x["id"]) not in edges:
            probs.append(f"{x['name']} needs arrows to and from the coordinator.")
    return probs


def save_network(data):
    with LOCK:
        n = dict(data)
        if not n.get("id"):
            n["id"] = new_id()
            n["created"] = time.time()
        _safe_id(n["id"])
        n = validate_network(n)
        n["updated"] = time.time()
        _write(NETS / f"{n['id']}.json", n)
        return n


def delete_network(nid):
    with LOCK:
        (NETS / f"{_safe_id(nid)}.json").unlink(missing_ok=True)
        (MEM / f"{nid}.json").unlink(missing_ok=True)


def reset_memory(nid):
    with LOCK:
        (MEM / f"{_safe_id(nid)}.json").unlink(missing_ok=True)
        return {"ok": True, "reset_at": time.time()}


# ------------------------------------------------------------------ model call

def call_model(system, prompt, model, timeout=600):
    """One tool-free Claude Code call. Returns text plus exact usage and cost estimate."""
    exe = shutil.which("claude")
    if not exe:
        return {"ok": False, "error": "Claude Code CLI not found", "text": ""}
    fd, sp = tempfile.mkstemp(suffix=".txt", prefix="lab-sys-")
    os.close(fd)
    Path(sp).write_text(system, encoding="utf-8")
    t0 = time.time()
    try:
        r = subprocess.run([exe, "-p", "--output-format", "json", "--model", model, "--tools", "",
                            "--system-prompt-file", sp, "--strict-mcp-config", "--disable-slash-commands",
                            "--no-session-persistence"],
                           input=prompt, capture_output=True, text=True, encoding="utf-8",
                           timeout=timeout, cwd=str(LAB))
        try:
            j = json.loads(r.stdout)
        except json.JSONDecodeError:
            return {"ok": False, "error": (r.stderr or r.stdout or "no output")[-800:], "text": "",
                    "duration_ms": int((time.time() - t0) * 1000)}
        u = j.get("usage") or {}
        return {"ok": not j.get("is_error"), "error": j.get("api_error_status") if j.get("is_error") else None,
                "text": j.get("result") or "",
                "input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
                "cache_read": u.get("cache_read_input_tokens", 0), "cache_write": u.get("cache_creation_input_tokens", 0),
                "cost_usd": j.get("total_cost_usd") or 0.0, "duration_ms": j.get("duration_ms") or int((time.time() - t0) * 1000),
                "model": next(iter(j.get("modelUsage") or {}), model)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "text": "", "duration_ms": int((time.time() - t0) * 1000)}
    finally:
        Path(sp).unlink(missing_ok=True)


class UsageLimitHit(Exception):
    """Claude Code reported a usage/rate limit: the run ends as 'usage_limit' so a study can pause and retry later."""


LIMIT_PATTERNS = ("usage limit", "rate limit", "rate_limit", "429", "overloaded", "529", "quota", "too many requests")


def is_limit_error(res):
    txt = f"{res.get('error') or ''} {res.get('text') or ''}".lower()
    return not res.get("ok") and any(p in txt for p in LIMIT_PATTERNS)


def parse_reply(text):
    """Pull the JSON object out of a reply. Returns (obj, ok)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b <= a:
        return None, False
    try:
        obj = json.loads(t[a:b + 1])
        return (obj, True) if isinstance(obj, dict) else (None, False)
    except json.JSONDecodeError:
        return None, False


# ------------------------------------------------------------------ prompts

ROUNDS_FORMAT = """Reply with ONLY one JSON object, no other text:
{
  "notes": "one or two sentences: what you concluded this turn (private, for the log)",
  "messages": [{"to": "<agent name>", "kind": "finding|request|reply|correction|decision", "topic": "<2-4 words>",
                "content": "<your message>", "evidence_ids": ["<ids you rely on>"]{summary_field}}],{extra_fields}
  "final_answer": null,
  "status": "working"
}
Rules: you may only message these agents: {targets}. Send no message if you have nothing useful to add.
"kind": finding = something you observed; request = asking someone to do or check something; reply = answering a request;
correction = a previous finding was wrong; decision = a conclusion or instruction from the coordinator.
{protocol_rules}
{final_rule}
"status" is "working", "done" (you have nothing more to contribute) or "unresolved" (you believe it cannot be solved)."""

PROTOCOL_RULES = {
    "free": "",
    "D0": ("PROTOCOL D0 (coordinator-led): all communication goes through {coord}. Specialists report findings to {coord}; "
           "{coord} routes information between specialists, reconciles conflicts and decides. A message a specialist addresses "
           "to another specialist is delivered to {coord} instead, who decides whether to pass it on."),
    "V1": ("PROTOCOL V1 (relay bypass): communication goes through {coord}, EXCEPT that a finding or correction with evidence IDs "
           "may be sent directly to another specialist along a permitted bypass path; {coord} receives a short notice of it. "
           "Requests, replies and decisions always go through {coord}.{bypass}"),
    "V2": ("PROTOCOL V2 (pull information): communication goes through {coord}. When you send a finding, recipients first get only "
           "a short notice (your \"summary\", topic, evidence IDs and a handle like H3); they request the full content if they need it. "
           "Corrections are always delivered in full. To get the full content of a notice you received, add its handle to \"requests\"; "
           "it arrives next round."),
    "V3": ("PROTOCOL V3 (early escalation): communication goes through {coord}. If you find evidence that CONTRADICTS a claim someone "
           "relies on, raise an \"alert\" naming the evidence IDs, the contradicted claim and the agent whose work depends on it. "
           "That agent is paused until the owner of the evidence verifies. If you receive a verification request, answer it with "
           "\"verification\". Do not raise alerts for mere uncertainty; false alarms cost the team time."),
}
PROTOCOL_RULES.update({
    "A1": ("PROTOCOL A1 (inhibitory evidence routing): communication goes through {coord}. Cite the evidence IDs every claim rests on "
           "and keep each message under 100 words. The router does NOT pass on your wording: it forwards the original text of the "
           "evidence you cite (only to agents who don't already hold it) plus at most a one-line claim to {coord}. "
           "What you receive from others is therefore evidence, not their conclusions: judge it yourself."),
    "A2": ("PROTOCOL A2 (PACT action-state messages): communication goes through {coord}. Every message is a compact record with "
           "three fields: \"action\" (what you did, or what the recipient should do), \"state\" (the observed facts, with evidence IDs) "
           "and \"result\" (your finding or output). Each field is one short sentence (about 25 words at most). "
           "No reasoning, no restating what others said, no pleasantries. Only these records are kept in the team's history."),
    "B1": ("PROTOCOL B1 (adaptive hierarchy): {coord} stays the formal lead, but the team switches between two modes, announced "
           "each round. FLAT rounds: anyone may message anyone directly to share findings, ask and answer; no decisions and no final "
           "answer. CENTRALIZE rounds: specialists send {coord} one position message (content: hypothesis, evidence IDs, confidence, "
           "open questions); messages between specialists go to {coord}; {coord} synthesises, decides and may submit the final answer."),
    "B2": ("PROTOCOL B2 (facilitated sharing): communication goes through {coord}. A neutral Facilitator watches which private "
           "evidence each specialist has shared. If it nudges you, share the named evidence (finding with evidence IDs, the claim, "
           "and its relevance) or say plainly that it is not relevant. Before the final answer is accepted, {coord} must address "
           "any evidence that was never shared or never used."),
})

EXTRA_FIELDS = {
    "V2": '\n  "requests": [{"handle": "<H# of a notice you received and need in full>"}],',
    "V3": ('\n  "alert": null,   // or {"evidence_ids": [...], "claim": "<the contradicted claim>", "dependency": "<agent whose work is affected>"}'
           '\n  "verification": null,   // or {"alert_id": "<A#>", "verdict": "valid|false_alarm", "note": "..."}'),
}

FINAL_RULE_COORD = ('You are the coordinator: when the evidence supports it, set "final_answer" to '
                    '{"conclusion": "<root cause / answer>", "evidence_ids": [...], "plan": "<remediation / action plan>"}. '
                    'That ends the run.')
FINAL_RULE_OTHER = 'Leave "final_answer" null; only the coordinator submits the final answer.'
FINAL_RULE_ANY = ('There is no coordinator: if you are confident the team has the answer, you may set "final_answer" to '
                  '{"conclusion": "...", "evidence_ids": [...], "plan": "..."}. That ends the run.')

PIPE_FORMAT = """Reply with ONLY one JSON object, no other text:
{"notes": "one or two sentences for the log", "output": "<your work product, passed on to: {targets}>", "evidence_ids": []}"""


def system_for(net, node, nodes_by_id, out_targets, memory, coord_name=None, bypass_paths=None):
    r = role_def(net["pack"], node["role"])
    team = "\n".join(f"- {n['name']}: {role_def(net['pack'], n['role'])['job']}" for n in nodes_by_id.values())
    has_coord = any(role_def(net["pack"], n["role"]).get("coordinator") for n in nodes_by_id.values())
    targets = ", ".join(out_targets) or "(nobody; you cannot send messages)"
    if net["mode"] == "pipeline":
        fmt = PIPE_FORMAT.replace("{targets}", targets if out_targets else "the final result")
    else:
        proto = net.get("protocol", "free")
        rule = FINAL_RULE_COORD if r.get("coordinator") else FINAL_RULE_OTHER if has_coord else FINAL_RULE_ANY
        bypass = ""
        if proto == "V1" and bypass_paths:
            bypass = " Your permitted bypass paths: " + "; ".join(
                f"to {to}" + (f" (topics: {', '.join(t)})" if t else " (any topic)") for to, t in bypass_paths) + "."
        elif proto == "V1":
            bypass = " You have no bypass paths, so everything goes through the coordinator."
        rules = PROTOCOL_RULES[proto].replace("{coord}", coord_name or "the coordinator").replace("{bypass}", bypass)
        fmt = (ROUNDS_FORMAT.replace("{targets}", targets).replace("{final_rule}", rule).replace("{protocol_rules}", rules)
               .replace("{extra_fields}", EXTRA_FIELDS.get(proto, ""))
               .replace("{summary_field}", ', "summary": "<one-line notice for findings>"' if proto == "V2" else ""))
        if proto == "A2":  # PACT: the message body is an action/state/result record instead of free text
            fmt = fmt.replace('"content": "<your message>"',
                              '"action": "<what you did / what the recipient should do>", "state": "<observed facts>", '
                              '"result": "<your finding or output>"')
    mem = f"\n\nYour memory from earlier runs:\n{memory}" if memory else ""
    return (f"You are {node['name']}, an AI agent in a multi-agent team.\nYour role: {r['job']}\n{r['instructions']}\n\n"
            f"The team:\n{team}\n\nYou only see messages sent to you. Be concise and specific; every token costs.\n\n{fmt}{mem}")


# ------------------------------------------------------------------ runs

class Run:
    def __init__(self, net, prompt, case=None):
        self.net = json.loads(json.dumps(net))   # frozen snapshot
        self.id = new_id()
        self.dir = RUNS / self.id
        self.dir.mkdir(parents=True)
        self.prompt = prompt
        self.case = case
        self.nodes = {n["id"]: n for n in self.net["nodes"]}
        self.out = {nid: [e["to"] for e in self.net["edges"] if e["from"] == nid] for nid in self.nodes}
        self.inc = {nid: [e["from"] for e in self.net["edges"] if e["to"] == nid] for nid in self.nodes}
        self.name_to_id = {n["name"].lower(): n["id"] for n in self.nodes.values()}
        self.memory = _read(MEM / f"{self.net['id']}.json", {}) or {}
        self.events = 0
        self.cost = 0.0
        self.t0 = time.time()
        self.history = {nid: [] for nid in self.nodes}   # what each agent has seen and said, in order
        self.received = {nid: [] for nid in self.nodes}  # message texts received (for relay detection)
        coords = [nid for nid, n in self.nodes.items() if (role_def(self.net["pack"], n["role"]) or {}).get("coordinator")]
        self.coord = coords[0] if coords else None
        self.pending = []          # (due_round, to, item) inbox deliveries
        self.board, self.handles = {}, 0            # V2 notice handles -> full content
        self.alerts, self.alert_seq, self.alert_count, self.paused = {}, 0, {}, {}   # V3
        self.ev_owner = {}         # evidence id -> agent that first reported it (until cases define owners)
        self.evidence_text = {}    # evidence id -> original text (A1 forwards this instead of paraphrases)
        self.holdings = {nid: set() for nid in self.nodes}    # evidence ids each agent privately holds
        self.shared = {nid: set() for nid in self.nodes}      # own evidence ids an agent has cited in a message (B2)
        self.mode = None           # B1: "flat" | "centralize" for the current round
        self.mode_history = []
        self.gate_done = False     # B2: pre-decision check already applied
        self.deferred_final = None # B1: a final answer submitted in a flat round waits for a centralize round
        self.a1_sent = set()       # A1: (round, receiver, evidence id) already forwarded
        self.nudges = {}           # B2: facilitator nudges per agent
        self.released_this_round = False
        # Prompt lines like "@Demand: ..." are private to that agent; everything else is the shared brief.
        self.private, shared = {nid: [] for nid in self.nodes}, []
        for line in prompt.splitlines():
            mm = re.match(r"\s*@([\w-]+)\s*:\s*(.*)", line)
            target = self.name_to_id.get(mm.group(1).lower()) if mm else None
            if target:
                self.private[target].append(mm.group(2))
                for eid in re.findall(r"\b[A-Z]{1,3}-?\d+\b", mm.group(2)):
                    self.ev_owner.setdefault(eid, target)   # whoever privately holds the evidence owns it (V3 verification)
                    self.holdings[target].add(eid)
                # "E1: text E2: text" -> original evidence passages (used by A1)
                for eid, txt in re.findall(r"\b([A-Z]{1,3}-?\d+)\s*:\s*(.+?)(?=\s+[A-Z]{1,3}-?\d+\s*:|$)", mm.group(2)):
                    self.evidence_text.setdefault(eid, txt.strip())
            else:
                shared.append(line)
        self.shared_brief = "\n".join(shared).strip() or "(see your private information)"
        # Case runs: the case brief is the shared task; evidence is released to the owning role(s) on a fixed schedule.
        self.schedule = {}
        if case:
            extra = self.shared_brief if prompt.strip() and self.shared_brief != "(see your private information)" else ""
            self.shared_brief = case["brief"].strip() + (f"\n\nAdditional instructions: {extra}" if extra else "")
            for e in case.get("evidence", []):
                self.schedule.setdefault(int(e.get("release_round", 1)), []).append(e)
                owners = [nid for nid, n in self.nodes.items() if n["role"] == e.get("owner")]
                if owners:
                    self.ev_owner.setdefault(e["id"], owners[0])
        self.summary = {
            "id": self.id, "network_id": self.net["id"], "network_name": self.net["name"], "network": self.net,
            "prompt": prompt, "case_id": case.get("id") if case else None, "status": "running",
            "case": {k: case.get(k) for k in ("id", "title", "split", "status", "hash", "difficulty")} if case else None,
            "mode": self.net["mode"], "protocol": self.net["protocol"], "model": self.net["model"],
            "started": self.t0, "ended": None, "end_reason": None, "round": 0, "final_answer": None,
            "node_state": {nid: "idle" for nid in self.nodes}, "totals": {}, "agents": {}, "scores": {},
            "score_version": SCORE_VERSION, "events": 0,
        }
        self.lock = threading.Lock()
        if case:  # frozen copy of the exact case used, so scoring stays reproducible if the case is edited later
            _write(self.dir / "case.json", {k: v for k, v in case.items()})
        self.save()

    # -- logging
    def log(self, ev):
        ev["t"] = round(time.time() - self.t0, 3)
        with self.lock:
            ev["i"] = self.events
            self.events += 1
            with open(self.dir / "trace.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self.summary["events"] = self.events
        return ev

    def save(self, final=False):
        """Live status saves are best-effort (the trace is the source of truth); the final save must succeed."""
        with self.lock:
            try:
                _write(self.dir / "run.json", self.summary)
            except PermissionError:
                if final:
                    raise

    def set_state(self, nid, state):
        self.summary["node_state"][nid] = state
        self.save()

    def over_cap(self):
        c = self.net["caps"]
        if self.id in STOP:
            return "stopped"
        if self.cost >= c["max_cost_usd"]:
            return "budget_cap"
        if time.time() - self.t0 >= c["max_seconds"]:
            return "time_cap"
        return None

    # -- one agent turn
    def turn(self, nid, round_no, items):
        node = self.nodes[nid]
        proto = self.net["protocol"]
        bypass = None
        if proto == "free":
            targets = [self.nodes[t]["name"] for t in self.out[nid]]
        elif nid == self.coord:
            targets = [self.nodes[t]["name"] for t in self.out[nid]]
        else:
            targets = [self.nodes[self.coord]["name"]]
            if proto == "V1":
                bypass = [(self.nodes[e["to"]]["name"], e.get("topics") or []) for e in self.net["edges"]
                          if e["from"] == nid and e["to"] != self.coord]
                targets += [b[0] for b in bypass]
            if proto == "B1" and self.mode == "flat":
                targets = [n["name"] for k, n in self.nodes.items() if k != nid]
        system = system_for(self.net, node, self.nodes, targets, self.memory.get(nid, ""),
                            coord_name=self.nodes[self.coord]["name"] if self.coord else None, bypass_paths=bypass)
        lines = [f"TASK BRIEF:\n{self.shared_brief}"]
        if self.private.get(nid):
            lines.append("PRIVATE INFORMATION ONLY YOU HAVE (others only learn it if you tell them):\n" + "\n".join(self.private[nid]))
        if self.history[nid]:
            lines.append("WHAT HAS HAPPENED SO FAR (from your point of view):\n" + "\n".join(self.history[nid]))
        if items:
            lines.append("NEW FOR YOU THIS ROUND:\n" + "\n".join(it["text"] for it in items))
        if proto == "B1" and self.mode:
            lines.append("THIS IS A FLAT ROUND: message anyone directly; share findings, ask and answer. No decisions and no final answer this round."
                         if self.mode == "flat" else
                         ("THIS IS A CENTRALIZE ROUND: " + ("synthesise the positions, decide, and submit the final answer if the evidence supports it."
                          if nid == self.coord else "send the coordinator one position message: hypothesis, evidence IDs, confidence, open questions.")))
        lines.append(f"This is round {round_no}. Respond as {node['name']} with the JSON object only.")
        prompt = "\n\n".join(lines)
        self.set_state(nid, "thinking")
        attempts = 0
        while True:
            attempts += 1
            res = call_model(system, prompt, self.net["model"], timeout=min(600, self.net["caps"]["max_seconds"]))
            with self.lock:
                self.cost += res.get("cost_usd", 0.0)
            obj, ok = parse_reply(res["text"]) if res["ok"] else (None, False)
            self.log({"type": "call", "node": nid, "round": round_no, "attempt": attempts, "ok": res["ok"],
                      "parsed": ok, "error": res.get("error"), "input_tokens": res.get("input_tokens", 0),
                      "output_tokens": res.get("output_tokens", 0), "cache_read": res.get("cache_read", 0),
                      "cache_write": res.get("cache_write", 0), "cost_usd": res.get("cost_usd", 0.0),
                      "duration_ms": res.get("duration_ms", 0), "model": res.get("model"),
                      "exposed_message_ids": [it["id"] for it in items], "exposed_chars": sum(len(it["text"]) for it in items),
                      "prompt_chars": len(prompt) + len(system), "raw": res["text"][:4000]})
            if is_limit_error(res):
                self.set_state(nid, "idle")
                raise UsageLimitHit(str(res.get("error"))[:200])
            if ok or attempts >= 2 or not res["ok"] and res.get("error") == "timeout":
                break
            prompt += "\n\nYour previous reply was not a valid JSON object. Reply with the JSON object only."
        self.set_state(nid, "idle")
        if not ok:
            self.log({"type": "malformed", "node": nid, "round": round_no})
            return None
        return obj

    # -- evidence release (case runs)
    def release_evidence(self, rnd, inbox, upto=False):
        """Deliver this round's evidence to the agents holding the owning role. Never reveals clue/distractor type."""
        rounds = [r for r in sorted(self.schedule) if (r <= rnd if upto else r == rnd)]
        for r in rounds:
            for e in self.schedule.pop(r):
                owners = [nid for nid, n in self.nodes.items() if n["role"] == e.get("owner")]
                head = f"EVIDENCE {e['id']}" + (f" (CORRECTION to {e['corrects']})" if e.get("type") == "correction" and e.get("corrects") else "")
                text = f"{head} · source: {e.get('source') or 'n/a'} · {e['content']}"
                if not owners:
                    self.log({"type": "evidence", "evidence_id": e["id"], "round": rnd, "to": None, "owner_role": e.get("owner"),
                              "unassigned": True, "chars": len(text)})
                    continue
                self.evidence_text[e["id"]] = f"{head} · source: {e.get('source') or 'n/a'} · {e['content']}"
                for nid in owners:
                    ev = self.log({"type": "evidence", "evidence_id": e["id"], "round": rnd, "to": nid, "owner_role": e.get("owner"),
                                   "chars": len(text), "correction": e.get("type") == "correction"})
                    self.private[nid].append(text)
                    self.holdings[nid].add(e["id"])
                    self.released_this_round = True
                    if inbox is not None:
                        inbox[nid].append({"id": f"ev{ev['i']}", "from_name": "evidence",
                                           "text": f"[new evidence] {head} has arrived; it is listed under your private information."})

    # -- delivery helpers
    def push(self, to, due, text, src_id, sender_name="system"):
        """Queue an item for an agent's inbox; it becomes visible at the start of round `due`."""
        self.pending.append((due, to, {"id": src_id, "text": text, "from_name": sender_name}))

    def sys_note(self, to, due, what, text, **extra):
        """A protocol-generated notice (not written by an agent). Logged so its exposure cost is attributable."""
        ev = self.log({"type": "protocol", "what": what, "to": to, "round": due - 1, "content": text,
                       "chars": len(text), **extra})
        self.push(to, due, f"[system] {text}", ev.get("id") or f"p{ev['i']}")
        return ev

    def fmt_msg(self, sender_name, kind, topic, content, ev_ids, prefix=""):
        tag = f" ({kind}{', ' + topic if topic else ''})" if kind else ""
        return f"{prefix}[{sender_name}]{tag} {content}" + (f" (evidence: {', '.join(ev_ids)})" if ev_ids else "")

    def route(self, sender, rnd, msgs):
        """Apply the network's protocol to an agent's outgoing messages. Returns number of messages accepted."""
        proto, coord, sent = self.net["protocol"], self.coord, 0
        edges = {(e["from"], e["to"]): e for e in self.net["edges"]}
        sname = self.nodes[sender]["name"]
        for m in (msgs or [])[:10]:
            if not isinstance(m, dict):
                continue
            to_name = str(m.get("to", "")).strip().lstrip("@").lower()
            to = self.name_to_id.get(to_name)
            content = str(m.get("content", "")).strip()
            pact = None
            if proto == "A2":  # PACT: the delivered message is the action/state/result record only
                parts = {k: str(m.get(k, "")).strip() for k in ("action", "state", "result")}
                pact = sum(1 for v in parts.values() if v)
                if pact:
                    content = " | ".join(f"{k.upper()}: {v}" for k, v in parts.items() if v)
            if not content:
                continue
            kind = str(m.get("kind", "finding")).lower()
            kind = kind if kind in MSG_KINDS else "finding"
            topic = str(m.get("topic", "")).strip().lower()[:40]
            ev_ids = [str(x) for x in (m.get("evidence_ids") or []) if isinstance(x, (str, int))][:20]
            summary = str(m.get("summary", "")).strip()[:300]
            for e in ev_ids:
                self.ev_owner.setdefault(e, sender)
                if e in self.holdings[sender]:
                    self.shared[sender].add(e)
            if not to or to == sender:
                self.log({"type": "blocked", "from": sender, "to_name": to_name, "round": rnd,
                          "reason": "unknown agent" if not to else "self", "content": content[:500]})
                continue
            relay = any(difflib.SequenceMatcher(None, content, r).ratio() > 0.85 for r in self.received[sender])
            base = {"type": "message", "id": new_id(), "from": sender, "to": to, "round": rnd, "kind": kind, "topic": topic,
                    "content": content, "evidence_ids": ev_ids, "chars": len(content), "relay": relay,
                    "from_name": sname, "to_name": self.nodes[to]["name"]}
            if pact is not None:
                base.update(pact_fields=pact, grounded=bool(ev_ids))
            if self.mode:
                base["phase"] = self.mode

            # 1) free protocol: the arrows are the permissions
            if proto == "free":
                if (sender, to) not in edges:
                    self.log({"type": "blocked", "from": sender, "to_name": to_name, "round": rnd, "reason": "not connected",
                              "content": content[:500]})
                    continue
                rec = self.log({**base, "channel": "direct", "delivered_to": to})
                self.push(to, rnd + 1, self.fmt_msg(sname, None, None, content, ev_ids), rec["id"], sname)
                self.history[sender].append(f"(round {rnd}) you -> {self.nodes[to]['name']}: {content}")
                sent += 1
                continue

            # 2) coordinator-led protocols (D0 base)
            if proto == "B1" and self.mode == "flat" and kind == "decision":
                self.log({"type": "blocked", "from": sender, "to_name": to_name, "round": rnd,
                          "reason": "decisions wait for a centralize round (B1)", "content": content[:500]})
                continue
            spec_to_spec = sender != coord and to != coord
            channel, deliver_to = "direct", to
            if spec_to_spec and proto == "B1" and self.mode == "flat":
                pass  # flat round: specialists talk to each other directly
            elif spec_to_spec:
                e = edges.get((sender, to))
                eligible = (proto == "V1" and e is not None and kind in ("finding", "correction") and ev_ids
                            and (not e.get("topics") or any(t in topic for t in e["topics"])))
                if eligible:
                    channel = "bypass"
                else:
                    channel, deliver_to = "routed", coord
                    if proto == "V1" and e is not None:
                        self.log({"type": "protocol", "what": "bypass_ineligible", "from": sender, "to": to, "round": rnd,
                                  "reason": "needs a finding/correction with evidence IDs" + (" on a permitted topic" if e.get("topics") else "")})
            elif (sender, to) not in edges:
                self.log({"type": "blocked", "from": sender, "to_name": to_name, "round": rnd, "reason": "not connected",
                          "content": content[:500]})
                continue

            rec = self.log({**base, "channel": channel, "delivered_to": deliver_to})
            sent += 1
            self.history[sender].append(f"(round {rnd}) you -> {self.nodes[to]['name']}"
                                        + (" (routed via coordinator)" if channel == "routed" else "") + f": {content}")
            if proto == "A1" and sender != coord:
                self.a1_forward(sender, deliver_to, rnd, kind, content, ev_ids, rec["id"])
                continue
            if channel == "routed":
                prefix = f"(for {self.nodes[to]['name']}, routed to you) "
                self.push(coord, rnd + 1, self.fmt_msg(sname, kind, topic, content, ev_ids, prefix), rec["id"], sname)
                continue
            if channel == "bypass":
                self.push(to, rnd + 1, self.fmt_msg(sname, kind, topic, content, ev_ids, "(direct, bypassing the coordinator) "),
                          rec["id"], sname)
                short = " ".join(content.split()[:25]) + ("…" if len(content.split()) > 25 else "")
                self.sys_note(coord, rnd + 1, "bypass_notice",
                              f"{sname} sent {self.nodes[to]['name']} a {kind} directly (topic: {topic or 'n/a'}; evidence: "
                              f"{', '.join(ev_ids)}): \"{short}\"", ref=rec["id"])
                continue
            # direct delivery; V2 turns findings into notices + pull
            if proto == "V2" and kind == "finding":
                self.handles += 1
                h = f"H{self.handles}"
                self.board[h] = {"from": sender, "to": deliver_to, "content": content, "kind": kind, "topic": topic,
                                 "evidence_ids": ev_ids, "round": rnd, "msg_id": rec["id"], "requested": False}
                note = summary or (" ".join(content.split()[:20]) + ("…" if len(content.split()) > 20 else ""))
                text = (f"NOTICE {h} [{sname}] (finding, {topic or 'n/a'}): {note}"
                        + (f" (evidence: {', '.join(ev_ids)})" if ev_ids else "") + f". Request handle {h} for the full content.")
                self.log({"type": "protocol", "what": "notice", "handle": h, "from": sender, "to": deliver_to, "round": rnd,
                          "content": text, "chars": len(text), "full_chars": len(content), "ref": rec["id"]})
                self.push(deliver_to, rnd + 1, text, rec["id"], sname)
            else:
                self.push(deliver_to, rnd + 1, self.fmt_msg(sname, kind, topic, content, ev_ids), rec["id"], sname)
        return sent

    # -- B2 facilitated sharing (Alsobay et al. 2026; rule-based facilitator, no extra model calls)
    def b2_sharing(self):
        out = {}
        for nid in self.nodes:
            if nid == self.coord or not self.holdings[nid]:
                continue
            held = self.holdings[nid]
            out[nid] = {"held": sorted(held), "shared": sorted(self.shared[nid] & held), "unshared": sorted(held - self.shared[nid]),
                        "share": round(len(self.shared[nid] & held) / len(held), 3)}
        return out

    def b2_nudge(self, rnd):
        stats = {k: v for k, v in self.b2_sharing().items() if v["unshared"] and self.nudges.get(k, 0) < 2 and k not in self.paused}
        if not stats:
            return
        nid = min(stats, key=lambda k: (stats[k]["share"], self.nudges.get(k, 0)))
        self.nudges[nid] = self.nudges.get(nid, 0) + 1
        ids = ", ".join(stats[nid]["unshared"])
        self.sys_note(nid, rnd + 1, "b2_nudge",
                      f"Facilitator: {self.nodes[nid]['name']}, you hold {ids} and haven't shared {'it' if ',' not in ids else 'them'} yet. "
                      f"Send the coordinator a finding (evidence IDs, the claim, and its relevance to the current hypothesis), "
                      f"or say plainly that it is not relevant.", share=stats[nid]["share"])

    def b2_gate(self, final, rnd):
        """Before accepting the decision once: the coordinator must address never-shared and shared-but-unused evidence."""
        self.gate_done = True
        stats = self.b2_sharing()
        unshared = {k: v["unshared"] for k, v in stats.items() if v["unshared"]}
        cited = {str(x).upper() for x in (final.get("evidence_ids") or [])}
        shared_all = {e for v in stats.values() for e in v["shared"]}
        unused = sorted(e for e in shared_all if e.upper() not in cited)
        self.log({"type": "protocol", "what": "b2_gate", "round": rnd, "unshared": unshared, "unused": unused,
                  "held": bool(unshared or unused)})
        if not unshared and not unused:
            return final
        parts = []
        if unshared:
            parts.append("never shared: " + "; ".join(f"{self.nodes[k]['name']} holds {', '.join(v)}" for k, v in unshared.items()))
            for k, v in unshared.items():
                self.sys_note(k, rnd + 1, "b2_gate_request",
                              f"Facilitator: the team is about to decide. Share {', '.join(v)} with the coordinator now, or say it is not relevant.")
        if unused:
            parts.append("shared but not used in your answer: " + ", ".join(unused))
        self.sys_note(self.coord, rnd + 1, "b2_gate_notice",
                      "Facilitator check before the decision: " + "; ".join(parts) +
                      ". Address each item (use it, or say why it doesn't matter), then submit your final answer again.")
        return None

    def a1_forward(self, sender, receiver, rnd, kind, content, ev_ids, msg_id):
        """A1 inhibitory routing (Siboni et al. 2026, simplified with declared evidence IDs instead of lexical matching):
        deliver the ORIGINAL text of each cited evidence item, once per receiver per round, and cancel ('inhibit') copies
        the receiver already holds. The sender's own wording is not forwarded, except a one-line claim to the coordinator."""
        sname = self.nodes[sender]["name"]
        for eid in ev_ids:
            if eid not in self.evidence_text:
                self.log({"type": "protocol", "what": "a1_invalid_id", "from": sender, "to": receiver, "round": rnd, "evidence_id": eid})
                continue
            key = (rnd, receiver, eid)
            if eid in self.holdings[receiver] or key in self.a1_sent:
                self.log({"type": "protocol", "what": "a1_inhibited", "from": sender, "to": receiver, "round": rnd, "evidence_id": eid,
                          "reason": "receiver already holds it" if eid in self.holdings[receiver] else "already forwarded this round"})
                continue
            self.a1_sent.add(key)
            text = f"[forwarded evidence, cited by {sname}] {self.evidence_text[eid]}"
            ev = self.log({"type": "protocol", "what": "a1_forward", "from": sender, "to": receiver, "round": rnd, "evidence_id": eid,
                           "content": text, "chars": len(text), "ref": msg_id})
            self.push(receiver, rnd + 1, text, f"p{ev['i']}", sname)
        if receiver == self.coord or not ev_ids:
            words = content.split()
            claim = " ".join(words[:30]) + ("…" if len(words) > 30 else "")
            if receiver == self.coord:
                text = f"[{sname}] ({kind}, one-line claim) {claim}" + (f" (evidence: {', '.join(ev_ids)})" if ev_ids else "")
                ev = self.log({"type": "protocol", "what": "a1_claim", "from": sender, "to": receiver, "round": rnd,
                               "content": text, "chars": len(text), "full_chars": len(content), "ref": msg_id})
                self.push(receiver, rnd + 1, text, f"p{ev['i']}", sname)
            else:
                self.log({"type": "protocol", "what": "a1_stripped", "from": sender, "to": receiver, "round": rnd,
                          "full_chars": len(content), "ref": msg_id})

    def handle_requests(self, nid, rnd, reqs):
        for r in (reqs or [])[:10]:
            h = str((r or {}).get("handle", "") if isinstance(r, dict) else r).strip().upper()
            b = self.board.get(h)
            if not b or b["to"] != nid:
                self.log({"type": "blocked", "from": nid, "to_name": h, "round": rnd,
                          "reason": "unknown handle" if not b else "not a recipient of this notice", "content": ""})
                continue
            b["requested"] = True
            self.log({"type": "protocol", "what": "pull_request", "handle": h, "from": nid, "to": b["from"], "round": rnd})
            text = self.fmt_msg(self.nodes[b["from"]]["name"], b["kind"], b["topic"], b["content"], b["evidence_ids"],
                                f"FULL CONTENT of {h}: ")
            ev = self.log({"type": "protocol", "what": "pull_delivered", "handle": h, "to": nid, "round": rnd,
                           "content": b["content"], "chars": len(b["content"]), "delay_rounds": rnd + 1 - b["round"]})
            self.push(nid, rnd + 1, text, f"p{ev['i']}", self.nodes[b["from"]]["name"])

    def handle_alert(self, nid, rnd, a):
        if not isinstance(a, dict) or not a.get("claim"):
            return
        if self.alert_count.get(nid, 0) >= self.net["caps"]["max_alerts"]:
            self.log({"type": "blocked", "from": nid, "to_name": "alert", "round": rnd, "reason": "alert limit reached", "content": str(a)[:300]})
            return
        self.alert_count[nid] = self.alert_count.get(nid, 0) + 1
        self.alert_seq += 1
        aid = f"A{self.alert_seq}"
        ev_ids = [str(x) for x in (a.get("evidence_ids") or [])][:10]
        dep = self.name_to_id.get(str(a.get("dependency", "")).strip().lstrip("@").lower())
        if dep in (self.coord, nid):
            dep = None  # the coordinator and the alerter are never paused
        owner = next((self.ev_owner[e] for e in ev_ids if e in self.ev_owner and self.ev_owner[e] != nid), None) or self.coord
        claim = str(a["claim"])[:400]
        self.alerts[aid] = {"by": nid, "owner": owner, "dependency": dep, "claim": claim, "evidence_ids": ev_ids,
                            "round": rnd, "open": True}
        self.log({"type": "protocol", "what": "alert", "alert_id": aid, "from": nid, "owner": owner, "dependency": dep,
                  "claim": claim, "evidence_ids": ev_ids, "round": rnd})
        sname = self.nodes[nid]["name"]
        if dep:
            self.paused[dep] = aid
            self.log({"type": "protocol", "what": "pause", "node": dep, "alert_id": aid, "round": rnd})
        self.sys_note(owner, rnd + 1, "verify_request",
                      f"VERIFY {aid}: {sname} says evidence {', '.join(ev_ids) or '(none given)'} contradicts the claim \"{claim}\". "
                      f"Check it and reply with \"verification\": {{\"alert_id\": \"{aid}\", \"verdict\": \"valid|false_alarm\", \"note\": \"...\"}}.",
                      alert_id=aid)
        if owner != self.coord and nid != self.coord:
            self.sys_note(self.coord, rnd + 1, "alert_notice",
                          f"ALERT {aid} by {sname}: claim \"{claim}\" contradicted by {', '.join(ev_ids)}."
                          + (f" {self.nodes[dep]['name']} is paused until {self.nodes[owner]['name']} verifies." if dep else ""),
                          alert_id=aid)

    def resolve_alert(self, aid, rnd, verdict, note, by, timeout=False):
        a = self.alerts.get(aid)
        if not a or not a["open"]:
            return
        a.update(open=False, verdict=verdict, resolved_round=rnd, note=note)
        self.log({"type": "protocol", "what": "alert_timeout" if timeout else "resolution", "alert_id": aid, "verdict": verdict,
                  "by": by, "round": rnd, "note": note, "alerter": a["by"], "recovery_rounds": rnd - a["round"]})
        if a["dependency"] and self.paused.get(a["dependency"]) == aid:
            del self.paused[a["dependency"]]
            self.log({"type": "protocol", "what": "resume", "node": a["dependency"], "alert_id": aid, "round": rnd})
        text = (f"{aid} {'timed out without verification' if timeout else 'resolved by ' + self.nodes[by]['name']}: "
                f"verdict {verdict}. {note}").strip()
        for who in {a["by"], a["dependency"], self.coord} - {None, by}:
            self.sys_note(who, rnd + 1, "resolution_notice", text, alert_id=aid)

    # -- modes
    def run_rounds(self):
        coord = [self.coord] if self.coord else []
        proto = self.net["protocol"]
        inbox = {nid: [] for nid in self.nodes}
        for rnd in range(1, self.net["caps"]["max_rounds"] + 1):
            reason = self.over_cap()
            if reason:
                return reason
            self.summary["round"] = rnd
            self.log({"type": "round", "round": rnd})
            # V3: alerts that waited too long resume automatically
            for aid, a in list(self.alerts.items()):
                if a["open"] and rnd - a["round"] > self.net["caps"]["alert_timeout_rounds"]:
                    self.resolve_alert(aid, rnd, "unverified", "", a["owner"], timeout=True)
            due = [p for p in self.pending if p[0] <= rnd]
            self.pending = [p for p in self.pending if p[0] > rnd]
            for _, to, it in due:
                inbox[to].append(it)
            self.released_this_round = False
            self.release_evidence(rnd, inbox)
            switched = False
            if proto == "B1":  # adaptive hierarchy: flat while evidence is arriving, then centralize to synthesise
                recent = self.mode_history[-2:]
                want = "flat" if (rnd <= 2 or self.released_this_round) else "centralize"
                if want == "flat" and len(recent) == 2 and all(m == "flat" for m in recent) and rnd > 2:
                    want = "centralize"   # never more than two flat rounds in a row: the lead pulls things together
                switched = bool(self.mode_history) and want != self.mode_history[-1]
                self.mode = want
                self.mode_history.append(want)
                self.log({"type": "protocol", "what": "mode", "mode": want, "round": rnd, "switched": switched})
            for nid, aid in self.paused.items():
                self.log({"type": "wait", "node": nid, "round": rnd, "reason": f"paused by {aid}"})
            actors = [nid for nid in self.nodes if (rnd == 1 or inbox[nid] or switched) and nid not in self.paused]
            if not actors:
                if self.pending or self.schedule:  # messages in flight or evidence still to be released
                    continue
                return "deadlock" if self.paused else "stalled"
            batch = {nid: inbox[nid] for nid in actors}
            for nid in actors:
                inbox[nid] = []
                for it in batch[nid]:
                    self.history[nid].append(f"(round {rnd - 1 if rnd > 1 else 1}) {it['text']}")
                    self.received[nid].append(it["text"])
            with ThreadPoolExecutor(max_workers=self.net["caps"]["concurrency"]) as ex:
                results = dict(zip(actors, ex.map(lambda nid: self.turn(nid, rnd, batch[nid]), actors)))
            final, unresolved = None, False
            for nid in actors:  # apply every turn of the round in a fixed order, then decide how the run continues
                obj = results[nid]
                if obj is None:
                    continue
                if obj.get("notes") and proto != "A2":   # PACT keeps reasoning out of the history
                    self.history[nid].append(f"(round {rnd}) your note: {obj['notes']}")
                sent = self.route(nid, rnd, obj.get("messages"))
                if proto == "V2":
                    self.handle_requests(nid, rnd, obj.get("requests"))
                if proto == "V3":
                    if obj.get("alert"):
                        self.handle_alert(nid, rnd, obj["alert"])
                    v = obj.get("verification")
                    if isinstance(v, dict):
                        aid = str(v.get("alert_id", "")).upper()
                        a = self.alerts.get(aid)
                        if a and a["open"] and a["owner"] == nid:
                            verdict = "valid" if str(v.get("verdict", "")).lower().startswith("valid") else "false_alarm"
                            self.resolve_alert(aid, rnd, verdict, str(v.get("note", ""))[:400], nid)
                fa = obj.get("final_answer")
                status = str(obj.get("status", "working"))
                acted = sent or obj.get("requests") or obj.get("alert") or obj.get("verification")
                self.log({"type": "turn", "node": nid, "round": rnd, "notes": str(obj.get("notes", ""))[:1000],
                          "sent": sent, "acted": bool(acted), "status": status, "final": bool(fa)})
                if fa and isinstance(fa, dict) and (nid in coord or not coord) and final is None:
                    final = {"by": nid, "round": rnd, **{k: fa.get(k) for k in ("conclusion", "evidence_ids", "plan")}}
                if status == "unresolved" and nid in coord:
                    unresolved = True
            if proto == "B1":
                if final and self.mode == "flat":
                    self.deferred_final = final
                    self.log({"type": "protocol", "what": "final_deferred", "round": rnd, "node": final["by"]})
                    self.sys_note(final["by"], rnd + 1, "final_deferred_notice",
                                  "Your final answer was held: decisions are taken in CENTRALIZE rounds. Confirm or revise it then.")
                    final = None
                elif not final and self.mode == "centralize" and self.deferred_final and not any(
                        p[1] == self.coord for p in self.pending):
                    final = dict(self.deferred_final, round=rnd)   # the held answer stands if nothing new came in
            if proto == "B2":
                if final and not self.gate_done:
                    final = self.b2_gate(final, rnd)
                self.b2_nudge(rnd)
            self.save()
            if final:
                self.summary["final_answer"] = final
                self.log({"type": "final", "node": final["by"], "round": rnd, "answer": final})
                return "final_answer"
            if unresolved:
                return "unresolved"
        return "round_cap"

    def run_pipeline(self):
        order, indeg = [], {nid: len(self.inc[nid]) for nid in self.nodes}
        ready = [nid for nid in self.nodes if indeg[nid] == 0]
        while ready:
            nid = ready.pop(0)
            order.append(nid)
            for t in self.out[nid]:
                indeg[t] -= 1
                if indeg[t] == 0:
                    ready.append(t)
        if len(order) != len(self.nodes):
            self.log({"type": "error", "message": "Pipeline mode needs connections without loops."})
            return "invalid_network"
        self.release_evidence(10 ** 6, None, upto=True)  # no rounds in a pipeline: every agent gets all its evidence up front
        outputs = {}
        for step, nid in enumerate(order, 1):
            reason = self.over_cap()
            if reason:
                return reason
            self.summary["round"] = step
            self.log({"type": "round", "round": step})
            inputs = [{"id": outputs[s]["id"], "from_name": self.nodes[s]["name"],
                       "text": self.fmt_msg(self.nodes[s]["name"], None, None, outputs[s]["content"], outputs[s]["evidence_ids"])}
                      for s in self.inc[nid] if s in outputs]
            for m in inputs:
                self.received[nid].append(outputs[[s for s in self.inc[nid] if s in outputs and outputs[s]["id"] == m["id"]][0]]["content"])
            obj = self.turn(nid, step, inputs)
            if obj is None:
                continue
            content = str(obj.get("output", "")).strip()
            ev_ids = [str(x) for x in (obj.get("evidence_ids") or [])][:20]
            rec = {"id": new_id(), "content": content, "evidence_ids": ev_ids}
            outputs[nid] = rec
            self.log({"type": "turn", "node": nid, "round": step, "notes": str(obj.get("notes", ""))[:1000],
                      "sent": len(self.out[nid]), "status": "done", "final": not self.out[nid]})
            for t in self.out[nid]:
                relay = any(difflib.SequenceMatcher(None, content, r).ratio() > 0.85 for r in self.received[nid])
                self.log({"type": "message", "id": rec["id"] if t == self.out[nid][0] else new_id(), "from": nid, "to": t,
                          "round": step, "content": content, "evidence_ids": ev_ids, "chars": len(content), "relay": relay,
                          "channel": "direct", "delivered_to": t, "kind": "finding", "topic": "",
                          "from_name": self.nodes[nid]["name"], "to_name": self.nodes[t]["name"]})
        sinks = [nid for nid in order if not self.out[nid] and nid in outputs]
        if sinks:
            last = sinks[-1]
            self.summary["final_answer"] = {"by": last, "round": self.summary["round"],
                                            "conclusion": "\n\n".join(outputs[s]["content"] for s in sinks),
                                            "evidence_ids": sorted({e for s in sinks for e in outputs[s]["evidence_ids"]}), "plan": ""}
            self.log({"type": "final", "node": last, "round": self.summary["round"], "answer": self.summary["final_answer"]})
            return "final_answer"
        return "no_output"

    def execute(self):
        ACTIVE.add(self.id)
        LIVE[self.id] = self
        reason = "error"
        try:
            self.log({"type": "start", "prompt": self.prompt, "mode": self.net["mode"], "protocol": self.net["protocol"],
                      "model": self.net["model"], "caps": self.net["caps"]})
            reason = self.run_pipeline() if self.net["mode"] == "pipeline" else self.run_rounds()
        except UsageLimitHit as e:
            self.log({"type": "error", "message": f"Usage limit reached: {e}"})
            reason = "usage_limit"
        except Exception as e:  # noqa: BLE001 - any crash is recorded as the run's end
            self.log({"type": "error", "message": f"{type(e).__name__}: {e}"})
            reason = "error"
        finally:
            if any(self.holdings.values()):
                self.log({"type": "protocol", "what": "sharing_summary", "stats": self.b2_sharing(),
                          "modes": self.mode_history or None})
            self.log({"type": "end", "reason": reason})
            self.summary.update(status="stopped" if reason == "stopped" else "done", end_reason=reason, ended=time.time())
            self.summary["node_state"] = {nid: "idle" for nid in self.nodes}
            finalize(self.summary, load_trace(self.id))
            if self.case and reason != "usage_limit":
                self.summary["evaluation_status"] = "pending"
            self.save(final=True)
            if self.net.get("persist_memory") and not self.summary.get("study_id"):
                self.remember()
            if self.case and reason != "usage_limit" and not self.summary.get("study_id"):
                scoring.evaluate_async(self.id)   # study runs are scored synchronously by the study runner
            ACTIVE.discard(self.id)
            STOP.discard(self.id)
            LIVE.pop(self.id, None)

    def remember(self):
        with LOCK:
            mem = _read(MEM / f"{self.net['id']}.json", {}) or {}
            fa = self.summary.get("final_answer") or {}
            for nid in self.nodes:
                notes = [h for h in self.history[nid] if "your note:" in h][-2:]
                line = f"Run on '{self.prompt[:80]}': outcome {self.summary['end_reason']}"
                if fa.get("conclusion"):
                    line += f"; team conclusion: {str(fa['conclusion'])[:200]}"
                if notes:
                    line += "; your last notes: " + " | ".join(n.split('your note:', 1)[1].strip() for n in notes)
                mem[nid] = (mem.get(nid, "") + "\n- " + line)[-3000:]
            _write(MEM / f"{self.net['id']}.json", mem)


def start_run(network_id, prompt, case_id=None):
    net = validate_network(get_network(network_id))
    if not net["nodes"]:
        raise ValueError("Add at least one agent first.")
    probs = protocol_problems(net)
    if probs:
        raise ValueError(" ".join(probs) + " Use 'Apply protocol layout' to fix the arrows.")
    case = None
    if case_id:
        case = cases.get_case(case_id)
        if case.get("domain") != net["pack"]:
            raise ValueError(f"This case is for the {ROLE_PACKS.get(case.get('domain'), {}).get('name', case.get('domain'))} pack, "
                             f"but the network uses {ROLE_PACKS[net['pack']]['name']} roles.")
        roles = {n["role"] for n in net["nodes"]}
        missing = sorted({e["owner"] for e in case.get("evidence", [])} - roles)
        if missing:
            names = [role_def(net["pack"], k)["name"] for k in missing]
            raise ValueError(f"No agent holds the role(s) {', '.join(names)}, so their evidence would never be seen. Add them to the canvas.")
    elif not prompt.strip():
        raise ValueError("Enter a prompt or pick a case to start the run.")
    run = Run(net, prompt.strip(), case)
    threading.Thread(target=run.execute, daemon=True).start()
    return run.summary


def run_snapshot_sync(net, case, meta, prompt=""):
    """Run a network snapshot (not a saved network) on a case in the calling thread. Used by the study runner.
    `meta` (study_id, condition, rep, seed, plan_index) is stored in the run summary. Returns the final summary."""
    net = validate_network(json.loads(json.dumps(net)))
    probs = protocol_problems(net)
    if probs:
        raise ValueError(" ".join(probs))
    run = Run(net, prompt, case)
    run.summary.update(meta)
    run.save()
    run.execute()
    return run.summary


def load_trace(rid):
    p = RUNS / _safe_id(rid) / "trace.jsonl"
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                break  # a line still being written; it will be picked up on the next poll
    return out


def get_run(rid, since=0):
    live = LIVE.get(_safe_id(rid))
    if live:
        with live.lock:
            s = json.loads(json.dumps(live.summary))
    else:
        s = _read(RUNS / rid / "run.json")
    if not s:
        raise KeyError(rid)
    trace = load_trace(rid)
    if s["status"] == "running":
        s["agents"] = agent_stats(s["network"], trace)
        s["totals"] = totals(s["agents"])
        s["protocol_metrics"] = protocol_metrics(s["network"], trace)
    return {"run": s, "events": trace[since:], "next": len(trace)}


def list_runs(network_id=None, limit=100):
    out = []
    for d in RUNS.iterdir():
        full = _read(d / "run.json")
        if full and (not network_id or full["network_id"] == network_id):
            s = {k: full[k] for k in ("id", "network_id", "network_name", "prompt", "status", "end_reason", "started",
                                      "ended", "mode", "protocol", "model", "totals", "round", "final_answer", "case_id", "case",
                                      "evaluation_status") if k in full}
            e = full.get("evaluation")
            if e:
                s["result"] = {"joint_success": e.get("joint_success"), "cause": e["cause"].get("pass"),
                               "evidence": e["evidence"].get("pass"), "remediation": e["remediation"].get("acceptable"),
                               "flags": len(e.get("flags", []))}
            out.append(s)
    return sorted(out, key=lambda r: -r["started"])[:limit]


def delete_run(rid):
    if rid in ACTIVE:
        raise ValueError("Stop the run first.")
    shutil.rmtree(RUNS / _safe_id(rid), ignore_errors=True)


# ------------------------------------------------------------------ stats and scores

def agent_stats(net, trace):
    st = {n["id"]: {"name": n["name"], "role": n["role"], "calls": 0, "failed_calls": 0, "input_tokens": 0, "output_tokens": 0,
                    "cache_read": 0, "cache_write": 0, "cost_usd": 0.0, "duration_ms": 0, "turns": 0, "msgs_sent": 0,
                    "msgs_received": 0, "chars_sent": 0, "exposures": 0, "exposed_chars": 0, "blocked": 0, "malformed": 0,
                    "empty_turns": 0, "relays": 0, "routed": 0, "bypasses": 0, "notices_sent": 0, "notices_unused": 0,
                    "pull_requests": 0, "alerts": 0, "false_alarms": 0, "verifications": 0, "paused_rounds": 0,
                    "evidence_cited": [], "final_answer": False, "work": []} for n in net["nodes"]}
    notices = {}
    for ev in trace:
        t = ev["type"]
        if t == "call" and ev["node"] in st:
            s = st[ev["node"]]
            s["calls"] += 1
            s["failed_calls"] += 0 if ev["ok"] else 1
            for k in ("input_tokens", "output_tokens", "cache_read", "cache_write", "duration_ms", "exposed_chars"):
                s[k] += ev.get(k, 0) or 0
            s["cost_usd"] += ev.get("cost_usd", 0) or 0
            s["exposures"] += len(ev.get("exposed_message_ids", []))
        elif t == "message":
            if ev["from"] in st:
                s = st[ev["from"]]
                s["msgs_sent"] += 1
                s["chars_sent"] += ev.get("chars", 0)
                s["relays"] += 1 if ev.get("relay") else 0
                s["routed"] += 1 if ev.get("channel") == "routed" else 0
                s["bypasses"] += 1 if ev.get("channel") == "bypass" else 0
                s["evidence_cited"] += ev.get("evidence_ids", [])
                via = " (via coordinator)" if ev.get("channel") == "routed" else " (bypass)" if ev.get("channel") == "bypass" else ""
                s["work"].append({"kind": "message", "round": ev["round"], "to": ev["to_name"] + via, "text": ev["content"][:300], "i": ev["i"]})
            dest = ev.get("delivered_to", ev["to"])
            if dest in st:
                st[dest]["msgs_received"] += 1
        elif t == "protocol":
            w = ev.get("what")
            if w == "notice" and ev.get("from") in st:
                st[ev["from"]]["notices_sent"] += 1
                notices[ev["handle"]] = ev["from"]
            elif w == "pull_request" and ev.get("from") in st:
                st[ev["from"]]["pull_requests"] += 1
                notices.pop(ev.get("handle"), None)
            elif w == "alert" and ev.get("from") in st:
                st[ev["from"]]["alerts"] += 1
                st[ev["from"]]["work"].append({"kind": "alert", "round": ev["round"], "to": "alert " + ev["alert_id"], "text": ev.get("claim", "")[:300], "i": ev["i"]})
            elif w == "resolution":
                if ev.get("by") in st:
                    st[ev["by"]]["verifications"] += 1
                if ev.get("verdict") == "false_alarm" and ev.get("alerter") in st:
                    st[ev["alerter"]]["false_alarms"] += 1
        elif t == "wait" and ev.get("node") in st:
            st[ev["node"]]["paused_rounds"] += 1
        elif t == "evidence" and ev.get("to") in st:
            st[ev["to"]]["evidence_received"] = st[ev["to"]].get("evidence_received", 0) + 1
        elif t == "blocked" and ev["from"] in st:
            st[ev["from"]]["blocked"] += 1
        elif t == "malformed" and ev["node"] in st:
            st[ev["node"]]["malformed"] += 1
        elif t == "turn" and ev["node"] in st:
            s = st[ev["node"]]
            s["turns"] += 1
            if not ev.get("sent") and not ev.get("acted") and not ev.get("final"):
                s["empty_turns"] += 1
        elif t == "final" and ev["node"] in st:
            st[ev["node"]]["final_answer"] = True
            st[ev["node"]]["work"].append({"kind": "final", "round": ev["round"], "text": str((ev.get("answer") or {}).get("conclusion", ""))[:300], "i": ev["i"]})
    for sender in notices.values():
        if sender in st:
            st[sender]["notices_unused"] += 1
    rounds = sum(1 for ev in trace if ev["type"] == "round")
    for s in st.values():
        s["cost_usd"] = round(s["cost_usd"], 6)
        s["evidence_cited"] = sorted(set(s["evidence_cited"]))
        s["avg_ms"] = int(s["duration_ms"] / s["calls"]) if s["calls"] else 0
        s["work"] = s["work"][-12:]
        s["total_input"] = s["input_tokens"] + s["cache_read"] + s["cache_write"]
        s["idle_rounds"] = max(0, rounds - s["turns"] - s["paused_rounds"])
    return st


def protocol_metrics(net, trace):
    """Run-level coordination and mechanism measures (proposal section 5)."""
    coord = next((n["id"] for n in net["nodes"] if (role_def(net["pack"], n["role"]) or {}).get("coordinator")), None)
    msgs = [e for e in trace if e["type"] == "message"]
    prot = [e for e in trace if e["type"] == "protocol"]
    calls = [e for e in trace if e["type"] == "call"]
    by = lambda w: [e for e in prot if e.get("what") == w]
    coord_msgs = [m for m in msgs if m["from"] == coord]
    spec_to_spec = [m for m in msgs if m["from"] != coord and m["to"] != coord]
    notices, pulls = by("notice"), by("pull_request")
    pulled = {p.get("handle") for p in pulls}
    alerts, res, tout = by("alert"), by("resolution"), by("alert_timeout")
    m = {
        "messages": len(msgs),
        "message_chars": sum(e.get("chars", 0) for e in msgs),
        "coordinator_calls": sum(1 for c in calls if c["node"] == coord),
        "handoffs": len([x for x in msgs if x.get("channel") in ("routed", "bypass")]) + len(coord_msgs),
        "relay_candidates": sum(1 for x in coord_msgs if x.get("relay")),
        "relay_share": round(sum(1 for x in coord_msgs if x.get("relay")) / len(coord_msgs), 3) if coord_msgs else None,
        "routed_via_coordinator": sum(1 for x in msgs if x.get("channel") == "routed"),
        "bypasses": sum(1 for x in msgs if x.get("channel") == "bypass"),
        "bypass_ineligible": len(by("bypass_ineligible")),
        "bypass_share": round(sum(1 for x in spec_to_spec if x.get("channel") == "bypass") / len(spec_to_spec), 3) if spec_to_spec else None,
        "notices": len(notices),
        "pull_requests": len(pulls),
        "unused_notices": sum(1 for n in notices if n.get("handle") not in pulled),
        "unused_detail_chars": sum(n.get("full_chars", 0) for n in notices if n.get("handle") not in pulled),
        "avg_pull_delay_rounds": round(sum(e.get("delay_rounds", 0) for e in by("pull_delivered")) / len(by("pull_delivered")), 2) if by("pull_delivered") else None,
        "alerts": len(alerts),
        "alerts_valid": sum(1 for r in res if r.get("verdict") == "valid"),
        "false_alarms": sum(1 for r in res if r.get("verdict") == "false_alarm"),
        "alert_timeouts": len(tout),
        "paused_agent_rounds": sum(1 for e in trace if e["type"] == "wait"),
        "avg_recovery_rounds": round(sum(r.get("recovery_rounds", 0) for r in res + tout) / len(res + tout), 2) if (res + tout) else None,
        "blocked": sum(1 for e in trace if e["type"] == "blocked"),
        "malformed": sum(1 for e in trace if e["type"] == "malformed"),
        "retries": sum(1 for c in calls if c.get("attempt", 1) > 1),
        "failed_calls": sum(1 for c in calls if not c.get("ok")),
    }
    # A1 inhibitory routing: how many cited copies were forwarded vs cancelled
    fwd, inh = by("a1_forward"), by("a1_inhibited")
    m.update({"a1_forwarded": len(fwd), "a1_inhibited": len(inh), "a1_invalid_ids": len(by("a1_invalid_id")),
              "a1_cancellation_ratio": round(len(inh) / (len(fwd) + len(inh)), 3) if (fwd or inh) else None,
              "a1_stripped_chars": sum(e.get("full_chars", 0) for e in by("a1_stripped") + by("a1_claim")) -
                                   sum(e.get("chars", 0) for e in by("a1_claim"))})
    # A2 PACT: record compactness and grounding
    pact = [x for x in msgs if "pact_fields" in x]
    m.update({"a2_records": len(pact), "a2_full_records": sum(1 for x in pact if x["pact_fields"] == 3),
              "a2_grounded_share": round(sum(1 for x in pact if x.get("grounded")) / len(pact), 3) if pact else None,
              "mean_message_chars": round(sum(x.get("chars", 0) for x in msgs) / len(msgs), 1) if msgs else None})
    # B1 adaptive hierarchy: phases and where the talking happened
    modes = by("mode")
    m.update({"b1_flat_rounds": sum(1 for x in modes if x.get("mode") == "flat"),
              "b1_centralize_rounds": sum(1 for x in modes if x.get("mode") == "centralize"),
              "b1_switches": sum(1 for x in modes if x.get("switched")),
              "b1_direct_flat_msgs": sum(1 for x in msgs if x.get("phase") == "flat" and x["from"] != coord and x["to"] != coord),
              "b1_hub_msgs": sum(1 for x in msgs if x["from"] == coord or x["to"] == coord),
              "b1_deferred_finals": len(by("final_deferred"))})
    # B2 facilitation (and evidence sharing for every protocol)
    share = by("sharing_summary")
    stats = (share[-1].get("stats") if share else None) or {}
    shares = [v["share"] for v in stats.values()]
    m.update({"b2_nudges": len(by("b2_nudge")), "b2_gate_holds": sum(1 for x in by("b2_gate") if x.get("held")),
              "evidence_share_mean": round(sum(shares) / len(shares), 3) if shares else None,
              "evidence_share_min": min(shares) if shares else None})
    return m


def totals(agents):
    keys = ("calls", "input_tokens", "output_tokens", "cache_read", "cache_write", "cost_usd", "msgs_sent", "blocked", "malformed", "relays")
    t = {k: sum(a[k] for a in agents.values()) for k in keys}
    t["cost_usd"] = round(t["cost_usd"], 6)
    return t


def score_agents(summary, agents, case_comps=None):
    """Agent score formula v1 (see docs/LAB_PLAN.md). Outcome and accuracy come from scoring.agent_components for case runs."""
    n = max(1, len(agents))
    fair = summary["network"]["caps"]["max_cost_usd"] / n
    out = {}
    for nid, a in agents.items():
        share = a["cost_usd"] / fair if fair else 0
        eff = 100.0 if share <= 0.5 else 0.0 if share >= 1.5 else 100.0 * (1.5 - share)
        if a["turns"] == 0 and a["calls"] == 0:
            eff = None
        comm = max(0.0, 100.0 - 15 * a["blocked"] - 10 * a["malformed"] - 5 * a["empty_turns"] - 8 * a["relays"]
                   - 5 * a.get("false_alarms", 0))
        cc = (case_comps or {}).get(nid, {})
        comps = {"outcome": cc.get("outcome"), "accuracy": cc.get("accuracy"), "efficiency": None if eff is None else round(eff, 1),
                 "communication": round(comm, 1) if a["calls"] else None}
        weights = {"outcome": 35, "accuracy": 25, "efficiency": 20, "communication": 20}
        avail = {k: v for k, v in comps.items() if v is not None}
        score = round(sum(v * weights[k] for k, v in avail.items()) / sum(weights[k] for k in avail), 1) if avail else None
        out[nid] = {"score": score, "partial": len(avail) < 4, "components": comps, "version": SCORE_VERSION}
    return out


def finalize(summary, trace):
    summary["agents"] = agent_stats(summary["network"], trace)
    summary["totals"] = totals(summary["agents"])
    summary["protocol_metrics"] = protocol_metrics(summary["network"], trace)
    summary["totals"]["seconds"] = round((summary.get("ended") or time.time()) - summary["started"], 1)
    summary["scores"] = score_agents(summary, summary["agents"])


def network_stats(nid):
    """All-time per-agent totals across this network's finished runs (agents matched by node id)."""
    agg, runs = {}, 0
    for r in list_runs(nid, limit=10000):
        s = _read(RUNS / r["id"] / "run.json")
        if not s or s["status"] == "running":
            continue
        runs += 1
        for node_id, a in s.get("agents", {}).items():
            g = agg.setdefault(node_id, {"name": a["name"], "runs": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0,
                                         "cache_read": 0, "cache_write": 0, "cost_usd": 0.0, "msgs_sent": 0,
                                         "msgs_received": 0, "score_sum": 0.0, "scored": 0})
            g["runs"] += 1
            for k in ("calls", "input_tokens", "output_tokens", "cache_read", "cache_write", "msgs_sent", "msgs_received"):
                g[k] += a.get(k, 0)
            g["cost_usd"] = round(g["cost_usd"] + a.get("cost_usd", 0), 6)
            sc = (s.get("scores", {}).get(node_id) or {}).get("score")
            if sc is not None:
                g["score_sum"] += sc
                g["scored"] += 1
    for g in agg.values():
        g["avg_score"] = round(g["score_sum"] / g["scored"], 1) if g["scored"] else None
    return {"runs": runs, "agents": agg}


# ------------------------------------------------------------------ HTTP routing

def handle(method, parts, body, query):
    """parts: path split after /api/lab, e.g. ['networks', '<id>', 'reset']. Returns (obj, status)."""
    try:
        if method == "GET":
            if parts == ["meta"]:
                return {"packs": ROLE_PACKS, "models": MODELS, "protocols": PROTOCOLS, "protocol_sources": PROTOCOL_SOURCES,
                        "default_caps": DEFAULT_CAPS,
                        "score_version": SCORE_VERSION}, 200
            if parts == ["networks"]:
                return {"networks": list_networks()}, 200
            if len(parts) == 3 and parts[0] == "networks" and parts[2] == "stats":
                return network_stats(_safe_id(parts[1])), 200
            if len(parts) == 3 and parts[0] == "networks" and parts[2] == "memory":
                return {"memory": _read(MEM / f"{_safe_id(parts[1])}.json", {}) or {}}, 200
            if parts == ["runs"]:
                return {"runs": list_runs(query.get("network"))}, 200
            if parts == ["cases"]:
                return {"cases": cases.list_cases(query.get("domain")), "evidence_types": cases.EVIDENCE_TYPES,
                        "features": cases.FEATURES}, 200
            if parts == ["review", "queue"]:
                return scoring.review_queue(), 200
            if parts == ["review", "agreement"]:
                return scoring.agreement(), 200
            if parts == ["cases", "registry"]:
                return cases.registry(query.get("domain")), 200
            if len(parts) == 2 and parts[0] == "cases":
                return {"case": cases.get_case(parts[1])}, 200
            if len(parts) == 2 and parts[0] == "runs":
                return get_run(parts[1], int(query.get("since", 0))), 200
        else:
            if parts == ["networks", "save"]:
                return {"network": save_network(body["network"])}, 200
            if parts == ["networks", "new"]:
                n = default_network(body.get("pack", "supply"))
                if body.get("empty"):
                    n["nodes"], n["edges"] = [], []
                n["name"] = (body.get("name") or n["name"])[:60]
                return {"network": save_network(n)}, 200
            if parts == ["networks", "delete"]:
                delete_network(body["id"])
                return {"ok": True}, 200
            if len(parts) == 3 and parts[0] == "networks" and parts[2] == "reset":
                return reset_memory(parts[1]), 200
            if parts == ["networks", "layout"]:
                return {"network": save_network(apply_protocol_layout(body["network"], body.get("protocol")))}, 200
            if parts == ["run"]:
                return {"run": start_run(body["network_id"], body.get("prompt", ""), body.get("case_id"))}, 200
            if parts == ["cases", "save"]:
                keys = lambda d: [r["key"] for r in ROLE_PACKS.get(d, {}).get("roles", [])]
                return {"case": cases.save_case(body["case"], keys, body.get("editor") or "you")}, 200
            if parts == ["cases", "transition"]:
                return {"case": cases.transition(body["id"], body["action"], body.get("who", ""), body.get("reason", ""))}, 200
            if parts == ["cases", "delete"]:
                cases.delete_case(body["id"])
                return {"ok": True}, 200
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "stop":
                STOP.add(_safe_id(parts[1]))
                return {"ok": True}, 200
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "evaluate":
                s = _read(RUNS / _safe_id(parts[1]) / "run.json")
                if not s or not s.get("case_id"):
                    raise ValueError("Only case runs can be scored.")
                if s.get("status") == "running":
                    raise ValueError("Wait for the run to finish.")
                scoring.evaluate_async(parts[1])
                return {"ok": True}, 200
            if parts == ["review", "sample"]:
                return scoring.build_sample(float(body.get("fraction", 0.2)), int(body.get("minimum", 5))), 200
            if parts == ["review", "submit"]:
                return {"review": scoring.submit_review(_safe_id(body["run_id"]), body, body.get("who"))}, 200
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "delete":
                delete_run(parts[1])
                return {"ok": True}, 200
        import studies  # late import (studies imports lab)
        r = studies.handle(method, parts, body, query)
        if r:
            return r
        return {"error": "not found"}, 404
    except KeyError:
        return {"error": "not found"}, 404
    except ValueError as e:
        return {"error": str(e)}, 400


def recover_interrupted():
    """Runs that were 'running' when the server stopped are marked as interrupted (kept, never deleted)."""
    for d in RUNS.iterdir():
        s = _read(d / "run.json")
        if s and s.get("status") == "running":
            s.update(status="interrupted", end_reason="server_stopped", ended=time.time(),
                     node_state={k: "idle" for k in s.get("node_state", {})})
            if s.get("case_id"):
                s["evaluation_status"] = "not_scored"
            finalize(s, load_trace(s["id"]))
            _write(d / "run.json", s)
