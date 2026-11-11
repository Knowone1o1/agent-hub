"""
Studies: the batch experiment of the proposal (conditions x cases x repetitions), run in the background.

Lifecycle: draft -> locked (preregistered: design, case versions, scoring version and caps are frozen) -> running <-> paused
-> done | stopped. Before the first run of a locked study, repetitions may be reduced equally across conditions
(the proposal's budget fallback); that is recorded as a deviation. Nothing else changes after locking.

The runner executes one plan item at a time in the preregistered random order, scores it, and moves on. When Claude
Code reports a usage limit, the attempt is kept (logged, its cost counted as an infrastructure retry), the study pauses,
and it resumes automatically after a wait. Every attempt stays on disk.

Analysis follows proposal section 5: joint success, cost per joint success (undefined when 0), cost per attempt,
coordination measures, mechanism measures, paired per-case outcomes, and bootstrap ranges that resample whole cases
(keeping their repetitions together). No significance claims.
"""

import csv
import io
import json
import random
import re
import threading
import time
import uuid
from pathlib import Path

import cases
import lab
import scoring

ROOT = Path(__file__).resolve().parent
STUDIES = ROOT / "data" / "lab" / "studies"
STUDIES.mkdir(parents=True, exist_ok=True)
LOCK = threading.RLock()
RUNNER = {"study_id": None, "thread": None, "pause": False, "stop": False}
CAP_REASONS = ("round_cap", "budget_cap", "time_cap")
CONDITIONS = tuple(k for k in lab.PROTOCOLS if k != "free")   # D0, V1-V3, A1-A2, B1-B2


def _path(sid):
    if not re.fullmatch(r"[a-z0-9-]{4,40}", sid or ""):
        raise KeyError(sid)
    return STUDIES / f"{sid}.json"


def get_study(sid):
    s = scoring._read(_path(sid))
    if not s:
        raise KeyError(sid)
    return s


def save_study(s):
    s["updated"] = time.time()
    scoring._write(_path(s["id"]), s)
    return s


def list_studies():
    out = [scoring._read(f) for f in STUDIES.glob("*.json")]
    return sorted([s for s in out if s], key=lambda s: -s.get("created", 0))


def log(s, event, **kw):
    s.setdefault("log", []).append({"t": time.time(), "event": event, **kw})


# ------------------------------------------------------------------ design

def create_study(data):
    net = lab.get_network(data["network_id"])
    kind = data.get("kind", "main")
    conds = [c for c in data.get("conditions", CONDITIONS) if c in CONDITIONS] or ["D0"]
    case_ids = data.get("case_ids") or []
    if not case_ids:
        raise ValueError("Pick at least one case.")
    for cid in case_ids:
        c = cases.get_case(cid)
        if c.get("domain") != net["pack"]:
            raise ValueError(f"Case {cid} is for another role pack than this network.")
    reps = max(1, min(int(data.get("repetitions", 3)), 10))
    s = {"id": uuid.uuid4().hex[:10], "name": (data.get("name") or f"{kind.title()} study")[:80], "kind": kind,
         "status": "draft", "created": time.time(), "network_id": net["id"], "network_name": net["name"],
         "conditions": conds, "case_ids": case_ids, "repetitions": reps, "seed": int(data.get("seed") or random.randint(1, 10 ** 6)),
         "ceiling_usd": float(data.get("ceiling_usd", 350)), "resume_wait_minutes": int(data.get("resume_wait_minutes", 30)),
         "plan": [], "log": []}
    log(s, "created")
    return save_study(s)


def update_draft(sid, data):
    s = get_study(sid)
    if s["status"] != "draft":
        raise ValueError("Only draft studies can be edited. Locked designs are preregistered.")
    for k in ("name", "conditions", "case_ids", "repetitions", "seed", "ceiling_usd", "resume_wait_minutes", "kind"):
        if k in data:
            s[k] = data[k]
    s["conditions"] = [c for c in s["conditions"] if c in CONDITIONS] or ["D0"]
    s["repetitions"] = max(1, min(int(s["repetitions"]), 10))
    return save_study(s)


def build_plan(s):
    """Conditions x cases x reps, one seed per (case, rep) shared by all conditions, then a seeded random order."""
    rng = random.Random(s["seed"])
    items = []
    for cid in s["case_ids"]:
        for rep in range(1, s["repetitions"] + 1):
            seed = rng.randint(1, 10 ** 9)
            for cond in s["conditions"]:
                items.append({"condition": cond, "case_id": cid, "rep": rep, "seed": seed})
    rng.shuffle(items)
    for i, it in enumerate(items):
        it.update(order=i + 1, status="queued", attempts=[], run_id=None, result=None)
    return items


def lock_study(sid, who, override_note=""):
    s = get_study(sid)
    if s["status"] != "draft":
        raise ValueError("The study is already locked.")
    who = (who or "").strip()
    if not who:
        raise ValueError("Enter your name for the preregistration record.")
    case_versions, snapshots = {}, {}
    unfrozen = []
    for cid in s["case_ids"]:
        c = cases.get_case(cid)
        case_versions[cid] = c["hash"]
        snapshots[cid] = c
        if c.get("status") != "frozen":
            unfrozen.append(cid)
    if s["kind"] == "main" and unfrozen and len(override_note.strip()) < 10:
        raise ValueError(f"{len(unfrozen)} case(s) are not frozen ({', '.join(unfrozen[:3])}...). Freeze them first, "
                         "or give a written reason to lock anyway (it is recorded as a deviation).")
    if s["kind"] == "main" and not feasibility_passed(s["network_id"]) and len(override_note.strip()) < 10:
        raise ValueError("No passing feasibility study for this network yet. Run one first, or give a written reason.")
    net = lab.get_network(s["network_id"])
    s["network_snapshot"] = json.loads(json.dumps(net))
    s["case_snapshots"] = snapshots   # runs always use these exact versions, even if a case is edited later
    s["prereg"] = {"locked_at": time.time(), "locked_by": who[:60], "case_versions": case_versions,
                   "scoring_version": scoring.SCORING_VERSION, "agent_score_version": lab.SCORE_VERSION,
                   "judge_model": scoring.JUDGE_MODEL, "acceptable_fraction": scoring.ACCEPTABLE_FRACTION,
                   "model": net["model"], "caps": net["caps"], "mode": net["mode"], "unfrozen_cases": unfrozen,
                   "override_note": override_note.strip() or None}
    s["plan"] = build_plan(s)
    s["status"] = "locked"
    log(s, "locked", by=who[:60], runs=len(s["plan"]))
    if override_note.strip():
        log(s, "deviation", note=f"Locked with override: {override_note.strip()}")
    return save_study(s)


def reduce_repetitions(sid, reps, who, reason):
    s = get_study(sid)
    if s["status"] not in ("locked",) or any(it["status"] != "queued" for it in s["plan"]):
        raise ValueError("Repetitions can only be reduced after locking and before the first run.")
    reps = int(reps)
    if not 1 <= reps < s["repetitions"]:
        raise ValueError("Choose fewer repetitions than now.")
    if len((reason or "").strip()) < 5:
        raise ValueError("Give a reason; it is recorded as a deviation.")
    old = s["repetitions"]
    s["plan"] = [it for it in s["plan"] if it["rep"] <= reps]
    for i, it in enumerate(s["plan"]):
        it["order"] = i + 1
    s["repetitions"] = reps
    log(s, "deviation", note=f"Repetitions reduced equally from {old} to {reps} before the first run: {reason.strip()}", by=who)
    return save_study(s)


# ------------------------------------------------------------------ runner

def start(sid):
    with LOCK:
        if RUNNER["thread"] and RUNNER["thread"].is_alive():
            if RUNNER["study_id"] == sid:
                RUNNER["pause"] = False
                return get_study(sid)
            raise ValueError("Another study is running. Pause it first (one study at a time keeps usage predictable).")
        s = get_study(sid)
        if s["status"] not in ("locked", "paused"):
            raise ValueError("Lock the study before running it." if s["status"] == "draft" else f"Study is {s['status']}.")
        s["status"] = "running"
        s.pop("resume_at", None)
        log(s, "started" if not any(it["attempts"] for it in s["plan"]) else "resumed")
        save_study(s)
        RUNNER.update(study_id=sid, pause=False, stop=False)
        RUNNER["thread"] = threading.Thread(target=_loop, args=(sid,), daemon=True)
        RUNNER["thread"].start()
        return s


def pause(sid, stop=False):
    with LOCK:
        s = get_study(sid)
        if RUNNER["study_id"] == sid and RUNNER["thread"] and RUNNER["thread"].is_alive():
            RUNNER["pause"] = not stop
            RUNNER["stop"] = stop
            for rid in list(lab.ACTIVE):   # stop the current run promptly; its attempt stays on record
                if (lab.LIVE.get(rid) and lab.LIVE[rid].summary.get("study_id") == sid):
                    lab.STOP.add(rid)
        s["status"] = "stopped" if stop else "paused"
        s.pop("resume_at", None)
        log(s, "stopped" if stop else "paused (by user)")
        return save_study(s)


def _loop(sid):
    while True:
        with LOCK:
            s = get_study(sid)
            if RUNNER["stop"] or RUNNER["pause"] or s["status"] != "running":
                return
            item = next((it for it in s["plan"] if it["status"] in ("queued", "running")), None)
            if not item:
                s["status"] = "done"
                log(s, "completed")
                save_study(s)
                return
            item["status"] = "running"
            save_study(s)
            net = lab.apply_protocol_layout(s["network_snapshot"], item["condition"])
            net["id"] = f"{sid}-{item['condition'].lower()}"
            net["name"] = f"{s['name']} · {item['condition']}"
            case = json.loads(json.dumps(s["case_snapshots"][item["case_id"]]))
        meta = {"study_id": sid, "condition": item["condition"], "rep": item["rep"], "seed": item["seed"], "plan_order": item["order"]}
        try:
            summ = lab.run_snapshot_sync(net, case, meta)
            reason = summ.get("end_reason")
            ev = scoring.evaluate_run(summ["id"]) if reason not in ("usage_limit", "stopped") else None
        except Exception as e:  # noqa: BLE001
            summ, reason, ev = None, "error", None
            err = f"{type(e).__name__}: {e}"
        with LOCK:
            s = get_study(sid)
            it = next(x for x in s["plan"] if x["order"] == item["order"])
            rid = summ["id"] if summ else None
            cost = (summ or {}).get("totals", {}).get("cost_usd", 0.0)
            it["attempts"].append({"run_id": rid, "end_reason": reason, "cost_usd": cost, "t": time.time()})
            if reason == "usage_limit":
                it["status"] = "queued"
                wait = s.get("resume_wait_minutes", 30)
                s["status"] = "paused"
                s["resume_at"] = time.time() + wait * 60
                log(s, "paused (usage limit)", resume_in_minutes=wait, run_id=rid)
                save_study(s)
                threading.Timer(wait * 60, _auto_resume, args=(sid, s["resume_at"])).start()
                return
            if reason == "stopped":
                it["status"] = "queued"   # the stopped attempt stays on record; the item is re-run on resume
                save_study(s)
                return
            if reason == "error" and not summ:
                it["status"] = "failed"
                log(s, "item error", order=it["order"], error=err)
            else:
                it["status"] = "done"
                it["run_id"] = rid
                e = ev or {}
                it["result"] = {"joint_success": e.get("joint_success"), "cause": (e.get("cause") or {}).get("pass"),
                                "evidence": (e.get("evidence") or {}).get("pass"),
                                "remediation": (e.get("remediation") or {}).get("acceptable"), "end_reason": reason,
                                "cost_usd": cost, "judge_cost_usd": ((e.get("judge") or {}).get("cost_usd") or 0.0)}
            save_study(s)


def _auto_resume(sid, expected):
    try:
        s = get_study(sid)
        if s["status"] == "paused" and s.get("resume_at") == expected:
            start(sid)
    except Exception:  # noqa: BLE001
        pass


def recover():
    """After a server restart: running studies become paused (the interrupted attempt is kept, the item re-queued)."""
    for s in list_studies():
        if s["status"] == "running" or any(it["status"] == "running" for it in s.get("plan", [])):
            for it in s["plan"]:
                if it["status"] == "running":
                    it["status"] = "queued"
            if s["status"] == "running":
                s["status"] = "paused"
                log(s, "paused (server restarted)")
            save_study(s)


# ------------------------------------------------------------------ budget and feasibility

def _runs_for_network(network_id):
    out = []
    for d in lab.RUNS.iterdir():
        r = scoring._read(d / "run.json")
        if r and r.get("case_id") and r.get("status") != "running" and (r.get("network_id") == network_id or
                                                                         str(r.get("network_id", "")).startswith(network_id)):
            out.append(r)
    return out


def study_runs(sid):
    """All attempts (including usage-limit and stopped attempts) belonging to a study."""
    out = []
    for d in lab.RUNS.iterdir():
        r = scoring._read(d / "run.json")
        if r and r.get("study_id") == sid:
            out.append(r)
    return out


def forecast(sid):
    """Price the plan from measured runs: per-condition mean team cost (+ judge), falling back to all measured runs."""
    s = get_study(sid)
    past = [r for st in list_studies() if st["network_id"] == s["network_id"] for r in study_runs(st["id"])]
    past = [r for r in past if r.get("end_reason") not in ("usage_limit", "stopped")]
    def mean(xs):
        return sum(xs) / len(xs) if xs else None
    all_team = [r["totals"].get("cost_usd", 0) for r in past]
    all_judge = [((r.get("evaluation") or {}).get("judge") or {}).get("cost_usd", 0) for r in past if r.get("evaluation")]
    per = {}
    total = 0.0
    n_runs = len(s["plan"]) or len(s["conditions"]) * len(s["case_ids"]) * s["repetitions"]
    for cond in s["conditions"]:
        cr = [r["totals"].get("cost_usd", 0) for r in past if r.get("condition") == cond]
        m = mean(cr) if cr else mean(all_team)
        per[cond] = {"measured_runs": len(cr), "mean_team_cost": m}
    mj = mean(all_judge) or 0.0
    for cond, v in per.items():
        if v["mean_team_cost"] is not None:
            total += (v["mean_team_cost"] + mj) * n_runs / len(s["conditions"])
    remaining = [it for it in s["plan"] if it["status"] != "done"] if s["plan"] else []
    spent = sum(a.get("cost_usd", 0) for it in s["plan"] for a in it["attempts"])
    est_remaining = sum(((per.get(it["condition"], {}).get("mean_team_cost") or 0) + mj) for it in remaining)
    return {"runs": n_runs, "measured_runs": len(past), "per_condition": per, "mean_judge_cost": mj,
            "estimate_total": round(total, 2) if past else None, "spent": round(spent, 4),
            "estimate_remaining": round(est_remaining, 2) if past else None, "ceiling_usd": s["ceiling_usd"],
            "over_ceiling": bool(past) and total > s["ceiling_usd"],
            "basis": "Claude Code API-equivalent estimates from earlier runs of this network" if past else "no measured runs yet"}


def feasibility(sid):
    """Gate (proposal section 6): D0 joint success >= 70% and resource-cap terminations <= 10% on development attempts."""
    s = get_study(sid)
    d0 = [it for it in s["plan"] if it["condition"] == "D0" and it["status"] == "done" and it.get("result")]
    attempts = [it for it in s["plan"] if it["status"] == "done" and it.get("result")]
    if not d0:
        return {"attempts": 0, "pass": None}
    succ = sum(1 for it in d0 if it["result"]["joint_success"])
    caps = sum(1 for it in attempts if it["result"]["end_reason"] in CAP_REASONS)
    sr, cr = succ / len(d0), caps / len(attempts)
    return {"attempts": len(attempts), "d0_attempts": len(d0), "d0_success_rate": round(sr, 3), "cap_termination_rate": round(cr, 3),
            "enough_attempts": len(attempts) >= 10, "pass": sr >= 0.7 and cr <= 0.1 and len(attempts) >= 10}


def feasibility_passed(network_id):
    return any(st["kind"] == "feasibility" and st["network_id"] == network_id and feasibility(st["id"]).get("pass")
               for st in list_studies())


# ------------------------------------------------------------------ analysis

def _mean(runs, key):
    vals = [(r.get("protocol_metrics") or {}).get(key) for r in runs]
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def _metrics_row(runs):
    """Aggregate a list of (final, scored) study runs."""
    n = len(runs)
    succ = sum(1 for r in runs if (r.get("evaluation") or {}).get("joint_success"))
    cost = sum(r["totals"].get("cost_usd", 0) for r in runs)
    pm = lambda k: sum((r.get("protocol_metrics") or {}).get(k) or 0 for r in runs)
    tot = lambda k: sum(r["totals"].get(k, 0) for r in runs)
    ev = lambda part, key: sum(1 for r in runs if ((r.get("evaluation") or {}).get(part) or {}).get(key))
    return {
        "attempts": n, "joint_successes": succ, "success_rate": round(succ / n, 3) if n else None,
        "cause_rate": round(ev("cause", "pass") / n, 3) if n else None,
        "evidence_rate": round(ev("evidence", "pass") / n, 3) if n else None,
        "remediation_rate": round(ev("remediation", "acceptable") / n, 3) if n else None,
        "total_cost": round(cost, 4), "cost_per_attempt": round(cost / n, 4) if n else None,
        "cost_per_success": round(cost / succ, 4) if succ else None,
        "input_tokens": tot("input_tokens"), "output_tokens": tot("output_tokens"), "cache_read": tot("cache_read"),
        "cache_write": tot("cache_write"), "calls": tot("calls"),
        "messages": pm("messages"), "coordinator_calls": pm("coordinator_calls"), "handoffs": pm("handoffs"),
        "relay_candidates": pm("relay_candidates"), "bypasses": pm("bypasses"), "notices": pm("notices"),
        "unused_notices": pm("unused_notices"), "unused_detail_chars": pm("unused_detail_chars"), "pull_requests": pm("pull_requests"),
        "alerts": pm("alerts"), "false_alarms": pm("false_alarms"), "paused_agent_rounds": pm("paused_agent_rounds"),
        "blocked": pm("blocked"), "malformed": pm("malformed"), "retries": pm("retries"),
        "a1_forwarded": pm("a1_forwarded"), "a1_inhibited": pm("a1_inhibited"),
        "a2_grounded_share": _mean(runs, "a2_grounded_share"), "mean_message_chars": _mean(runs, "mean_message_chars"),
        "b1_switches": pm("b1_switches"), "b1_direct_flat_msgs": pm("b1_direct_flat_msgs"),
        "a3_overrides": pm("a3_override"), "a3_uncertain": pm("a3_uncertain"), "a3_p_top": _mean(runs, "a3_p_top"),
        "a4_requests": pm("a4_requests"), "a4_replies": pm("a4_replies"), "a4_no_reply": pm("a4_no_reply"),
        "b2_standup_msgs": pm("b2_standup_msgs"), "b2_goal_changes": pm("b2_goal_changes"),
        "b3_held": pm("b3_held"), "b3_deduped": pm("b3_deduped"), "b3_urgent": pm("b3_urgent"),
        "b4_threads": pm("b4_threads"), "b4_expired": pm("b4_expired"), "b4_duplicates": pm("b4_duplicates"),
        "burstiness": _mean(runs, "burstiness"), "evidence_to_coordinator_delay": _mean(runs, "evidence_to_coordinator_delay"),
        "cross_domain_share": _mean(runs, "cross_domain_share"),
        "evidence_share_mean": _mean(runs, "evidence_share_mean"), "evidence_share_min": _mean(runs, "evidence_share_min"),
        "cap_terminations": sum(1 for r in runs if r.get("end_reason") in CAP_REASONS),
        "mean_seconds": round(sum(r["totals"].get("seconds", 0) for r in runs) / n, 1) if n else None,
        "mean_rounds": round(sum(r.get("round", 0) for r in runs) / n, 2) if n else None,
    }


def analyze(sid, boot=2000):
    s = get_study(sid)
    all_attempts = study_runs(sid)
    infra = [r for r in all_attempts if r.get("end_reason") in ("usage_limit", "stopped")]
    final_ids = {it["run_id"] for it in s["plan"] if it["status"] == "done" and it["run_id"]}
    runs = [r for r in all_attempts if r["id"] in final_ids and r.get("evaluation")]
    by_cond = {c: [r for r in runs if r.get("condition") == c] for c in s["conditions"]}
    rows = {c: _metrics_row(v) for c, v in by_cond.items()}
    for c in s["conditions"]:
        rows[c]["infrastructure_retry_cost"] = round(sum(r["totals"].get("cost_usd", 0) for r in infra if r.get("condition") == c), 4)
        rows[c]["mean_judge_cost"] = round(sum(((r.get("evaluation") or {}).get("judge") or {}).get("cost_usd", 0) for r in by_cond[c]) / len(by_cond[c]), 4) if by_cond[c] else None
    # paired per-case outcomes
    paired = []
    for cid in s["case_ids"]:
        row = {"case_id": cid}
        try:
            row["title"] = cases.get_case(cid)["title"]
        except KeyError:
            row["title"] = cid
        for c in s["conditions"]:
            rr = [r for r in by_cond[c] if r["case_id"] == cid]
            row[c] = {"attempts": len(rr), "successes": sum(1 for r in rr if r["evaluation"]["joint_success"]),
                      "mean_cost": round(sum(r["totals"]["cost_usd"] for r in rr) / len(rr), 4) if rr else None}
        paired.append(row)
    # bootstrap over whole cases (repetitions stay together)
    rng = random.Random(s["seed"])
    cids = [p["case_id"] for p in paired]
    per_case = {c: {cid: [r for r in by_cond[c] if r["case_id"] == cid] for cid in cids} for c in s["conditions"]}
    samples = {c: {"success_rate": [], "cost_per_success": []} for c in s["conditions"]}
    diffs = {c: {"success_rate": [], "cost_per_success": []} for c in s["conditions"] if c != "D0"}
    if cids and runs:
        for _ in range(boot):
            pick = [rng.choice(cids) for _ in cids]
            vals = {}
            for c in s["conditions"]:
                rr = [r for cid in pick for r in per_case[c][cid]]
                n = len(rr)
                succ = sum(1 for r in rr if r["evaluation"]["joint_success"])
                cost = sum(r["totals"]["cost_usd"] for r in rr)
                vals[c] = (succ / n if n else None, cost / succ if succ else None)
                if vals[c][0] is not None:
                    samples[c]["success_rate"].append(vals[c][0])
                if vals[c][1] is not None:
                    samples[c]["cost_per_success"].append(vals[c][1])
            if "D0" in vals:
                for c in diffs:
                    if vals.get(c) and vals[c][0] is not None and vals["D0"][0] is not None:
                        diffs[c]["success_rate"].append(vals[c][0] - vals["D0"][0])
                    if vals.get(c) and vals[c][1] is not None and vals["D0"][1] is not None:
                        diffs[c]["cost_per_success"].append(vals[c][1] - vals["D0"][1])

    def ci(xs):
        if len(xs) < 20:
            return None
        xs = sorted(xs)
        return [round(xs[int(0.025 * len(xs))], 4), round(xs[int(0.975 * len(xs)) - 1], 4)]
    ranges = {c: {k: ci(v) for k, v in samples[c].items()} for c in samples}
    diff_ranges = {c: {k: ci(v) for k, v in diffs[c].items()} for c in diffs}
    done = sum(1 for it in s["plan"] if it["status"] == "done")
    return {"study": {k: s.get(k) for k in ("id", "name", "kind", "status", "conditions", "case_ids", "repetitions", "seed", "prereg")},
            "progress": {"done": done, "total": len(s["plan"]), "failed": sum(1 for it in s["plan"] if it["status"] == "failed"),
                         "infrastructure_attempts": len(infra)},
            "conditions": rows, "paired": paired, "bootstrap": {"iterations": boot, "ranges": ranges, "vs_D0": diff_ranges,
                                                                "note": "Resamples whole cases with their repetitions; exploratory ranges, not significance tests."},
            "feasibility": feasibility(sid) if s["kind"] == "feasibility" else None,
            "notes": ["Costs are Claude Code API-equivalent estimates on a subscription, not provider-billed dollars.",
                      "Cost per success includes failed and capped runs; usage-limit and stopped attempts are reported separately as infrastructure retries.",
                      "Judge cost is evaluation overhead and is not included in team cost.",
                      "With few cases, differences smaller than the ranges shown are not evidence of preserved or improved reliability."]}


# ------------------------------------------------------------------ exports

EXTRA_METRICS = ["a1_forwarded", "a1_inhibited", "a1_cancellation_ratio", "a2_grounded_share", "mean_message_chars",
                 "a3_clusters", "a3_p_top", "a3_margin", "a3_uncertain", "a3_override", "a4_requests", "a4_replies", "a4_no_reply",
                 "b1_switches", "b1_direct_flat_msgs", "b1_deferred_finals", "b2_standup_msgs", "b2_goal_changes",
                 "b3_held", "b3_deduped", "b3_urgent", "b4_threads", "b4_expired", "b4_duplicates", "burstiness",
                 "evidence_to_coordinator_delay", "cross_domain_share",
                 "evidence_share_mean", "evidence_share_min"]
RUN_COLUMNS = ["study_id", "run_id", "plan_order", "condition", "case_id", "case_version", "split", "rep", "seed", "model",
               "end_reason", "rounds", "seconds", "joint_success", "cause_ok", "evidence_ok", "evidence_recall", "remediation_ok",
               "remediation_points", "remediation_max", "cost_usd", "judge_cost_usd", "input_tokens", "output_tokens", "cache_read",
               "cache_write", "calls", "messages", "coordinator_calls", "handoffs", "relay_candidates", "bypasses", "notices",
               "unused_notices", "pull_requests", "alerts", "false_alarms", "paused_agent_rounds", "blocked", "malformed",
               "retries", *EXTRA_METRICS, "flags", "human_review_points", "attempt_type"]


def _run_row(r, attempt_type):
    e, t, pm = r.get("evaluation") or {}, r.get("totals") or {}, r.get("protocol_metrics") or {}
    rem, ev = e.get("remediation") or {}, e.get("evidence") or {}
    return {"study_id": r.get("study_id"), "run_id": r["id"], "plan_order": r.get("plan_order"), "condition": r.get("condition"),
            "case_id": r.get("case_id"), "case_version": (r.get("case") or {}).get("hash"), "split": (r.get("case") or {}).get("split"),
            "rep": r.get("rep"), "seed": r.get("seed"), "model": r.get("model"), "end_reason": r.get("end_reason"),
            "rounds": r.get("round"), "seconds": t.get("seconds"), "joint_success": e.get("joint_success"),
            "cause_ok": (e.get("cause") or {}).get("pass"), "evidence_ok": ev.get("pass"), "evidence_recall": ev.get("recall"),
            "remediation_ok": rem.get("acceptable"), "remediation_points": rem.get("points"), "remediation_max": rem.get("max_points"),
            "cost_usd": t.get("cost_usd"), "judge_cost_usd": (e.get("judge") or {}).get("cost_usd"),
            **{k: t.get(k) for k in ("input_tokens", "output_tokens", "cache_read", "cache_write", "calls")},
            **{k: pm.get(k) for k in ("messages", "coordinator_calls", "handoffs", "relay_candidates", "bypasses", "notices",
                                      "unused_notices", "pull_requests", "alerts", "false_alarms", "paused_agent_rounds",
                                      "blocked", "malformed", "retries", *EXTRA_METRICS)},
            "flags": " | ".join(e.get("flags") or []), "human_review_points": (r.get("human_review") or {}).get("total"),
            "attempt_type": attempt_type}


def export(sid, what):
    """Returns (filename, content_type, text). Also writes it to data/lab/studies/<id>_exports/ for archiving."""
    s = get_study(sid)
    attempts = study_runs(sid)
    final_ids = {it["run_id"] for it in s["plan"] if it["run_id"]}
    out_dir = STUDIES / f"{sid}_exports"
    out_dir.mkdir(exist_ok=True)
    if what == "runs.csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=RUN_COLUMNS)
        w.writeheader()
        for r in sorted(attempts, key=lambda r: (r.get("plan_order") or 0, r["started"])):
            w.writerow(_run_row(r, "evaluated" if r["id"] in final_ids else "infrastructure_retry"))
        text, ctype = buf.getvalue(), "text/csv"
    elif what == "agents.csv":
        cols = ["run_id", "condition", "case_id", "rep", "agent", "role", "score", "outcome", "accuracy", "efficiency", "communication",
                "calls", "input_tokens", "output_tokens", "cache_read", "cache_write", "cost_usd", "msgs_sent", "msgs_received",
                "evidence_received", "routed", "bypasses", "notices_sent", "notices_unused", "pull_requests", "alerts", "false_alarms",
                "paused_rounds", "blocked", "malformed"]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        for r in attempts:
            if r["id"] not in final_ids:
                continue
            for nid, a in (r.get("agents") or {}).items():
                sc = (r.get("scores") or {}).get(nid, {})
                comp = sc.get("components") or {}
                w.writerow({"run_id": r["id"], "condition": r.get("condition"), "case_id": r.get("case_id"), "rep": r.get("rep"),
                            "agent": a["name"], "role": a["role"], "score": sc.get("score"), **{k: comp.get(k) for k in ("outcome", "accuracy", "efficiency", "communication")},
                            **{k: a.get(k) for k in cols[11:]}})
        text, ctype = buf.getvalue(), "text/csv"
    elif what == "traces.jsonl":
        lines = []
        for r in sorted(attempts, key=lambda r: r["started"]):
            for ev in lab.load_trace(r["id"]):
                lines.append(json.dumps({"run_id": r["id"], "condition": r.get("condition"), "case_id": r.get("case_id"), **ev}, ensure_ascii=False))
        text, ctype = "\n".join(lines) + "\n", "application/x-ndjson"
    elif what == "analysis.json":
        text, ctype = json.dumps({"analysis": analyze(sid), "design": {k: s.get(k) for k in ("id", "name", "kind", "conditions", "case_ids", "repetitions", "seed", "prereg", "log", "plan")}},
                                 indent=2, ensure_ascii=False), "application/json"
    else:
        raise KeyError(what)
    (out_dir / what).write_text(text, encoding="utf-8")
    return f"{s['name'].replace(' ', '_')}_{what}", ctype, text


# ------------------------------------------------------------------ HTTP

def handle(method, parts, body, query):
    if method == "GET":
        if parts == ["studies"]:
            return {"studies": list_studies(), "runner": {"study_id": RUNNER["study_id"],
                                                          "alive": bool(RUNNER["thread"] and RUNNER["thread"].is_alive())}}, 200
        if len(parts) == 2 and parts[0] == "studies":
            s = get_study(parts[1])
            return {"study": s, "forecast": forecast(s["id"]), "feasibility": feasibility(s["id"]) if s["kind"] == "feasibility" else None}, 200
        if len(parts) == 3 and parts[0] == "studies" and parts[2] == "analysis":
            return analyze(parts[1]), 200
        return None
    if parts == ["studies", "new"]:
        return {"study": create_study(body)}, 200
    if len(parts) == 3 and parts[0] == "studies":
        sid, act = parts[1], parts[2]
        if act == "update":
            return {"study": update_draft(sid, body)}, 200
        if act == "lock":
            return {"study": lock_study(sid, body.get("who"), body.get("override_note", ""))}, 200
        if act == "reduce":
            return {"study": reduce_repetitions(sid, body.get("repetitions"), body.get("who", ""), body.get("reason", ""))}, 200
        if act == "start":
            return {"study": start(sid)}, 200
        if act == "pause":
            return {"study": pause(sid)}, 200
        if act == "stop":
            return {"study": pause(sid, stop=True)}, 200
        if act == "delete":
            s = get_study(sid)
            if s["status"] not in ("draft",):
                raise ValueError("Only draft studies can be deleted; locked studies are part of the record.")
            _path(sid).unlink(missing_ok=True)
            return {"ok": True}, 200
    return None
