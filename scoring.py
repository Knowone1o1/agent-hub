"""
Scoring v1 for Agent Lab case runs (proposal section 5).

Joint task success = (i) correct root cause AND (ii) supporting evidence IDs AND (iii) acceptable remediation plan.
  (i)   cause: automatic keyword check against the answer key (every keyword group must match). A blinded Claude judge
        also gives a verdict; disagreements are flagged for human review (they never silently override the check).
  (ii)  evidence: the final answer cites every required evidence ID (recall = cited required / required).
  (iii) remediation: a blinded Claude judge scores the plan against the case rubric; acceptable = >= 60% of points.
The judge sees only the case brief, the answer key/rubric and the final answer: never the protocol, network, agents or run id.
Judge cost is recorded separately (evaluation overhead), not added to the team's run cost.

Per-agent outcome and accuracy (agent score v1, see docs/LAB_PLAN.md) are computed here as well.
Blinded human spot-checks are stored per run and compared with the judge (agreement statistics).
"""

import json
import random
import re
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "data" / "lab" / "runs"
REVIEW_FILE = ROOT / "data" / "lab" / "review_sample.json"

SCORING_VERSION = "v1"
JUDGE_MODEL = "sonnet"
ACCEPTABLE_FRACTION = 0.6
LOCK = threading.RLock()


def _read(p, default=None):
    for i in range(20):
        try:
            return json.loads(Path(p).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return default
        except PermissionError:
            time.sleep(0.02 * (i + 1))
    return default


def _write(p, obj):
    import os, uuid
    p = Path(p)
    tmp = p.with_name(f"{p.name}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    for i in range(40):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(0.025 * (i + 1))
    raise PermissionError(str(p))


def norm(s):
    s = (s or "").lower().replace("-", " ")
    return re.sub(r"\s+", " ", s)


# ------------------------------------------------------------------ automatic checks

def keyword_check(text, groups):
    t = norm(text)
    matched = []
    for g in groups or []:
        hit = next((w for w in g if norm(w) in t), None)
        matched.append({"group": g, "matched": hit})
    ok = bool(groups) and all(m["matched"] for m in matched)
    return {"pass": ok, "groups": matched}


def evidence_check(final, case):
    ak = case.get("answer_key") or {}
    # tolerate citations like "E3 (customs feed)" or "Logistics E3": keep only the IDs themselves
    cited = set()
    for x in final.get("evidence_ids") or []:
        found = re.findall(r"\b[A-Za-z]{1,4}-?\d{1,3}\b", str(x))
        cited |= {m.upper() for m in found} if found else {str(x).strip().upper()}
    req = [r.upper() for r in ak.get("required_evidence", [])]
    distractors = [e["id"].upper() for e in case.get("evidence", []) if e.get("type") == "distractor"]
    got = [r for r in req if r in cited]
    return {"required": req, "cited": sorted(cited), "present": got, "missing": [r for r in req if r not in cited],
            "recall": round(len(got) / len(req), 3) if req else None, "pass": bool(req) and len(got) == len(req),
            "distractors_cited": [d for d in distractors if d in cited]}


# ------------------------------------------------------------------ blinded judge

JUDGE_SYSTEM = """You are a strict, impartial evaluator of supply-chain and operations incident analyses.
You score ONE final answer against a written rubric. You know nothing about how the answer was produced; judge only the text.
Be literal: award a criterion's points only to the extent the answer actually contains that action or insight, concretely enough
to act on. Partial credit is allowed in whole points. Do not reward length or confident tone. Reply with ONLY a JSON object."""


def judge_prompt(case, final):
    ak = case.get("answer_key") or {}
    crit = "\n".join(f"{i + 1}. ({c['points']} pts) {c['criterion']}" for i, c in enumerate(ak.get("remediation_rubric", [])))
    return f"""CASE BRIEF:
{case.get('brief', '')}

REFERENCE ROOT CAUSE (answer key):
{ak.get('root_cause', '')}

REMEDIATION RUBRIC:
{crit}

THE ANSWER TO EVALUATE:
Conclusion: {final.get('conclusion') or '(none)'}
Plan: {final.get('plan') or '(none)'}

Return exactly:
{{"cause_correct": true|false, "cause_reason": "<one sentence: does the conclusion identify the reference root cause (not just a symptom or a distractor)?>",
  "criteria": [{{"index": 1, "points": <0..max>, "reason": "<short>"}}, ...one entry per rubric criterion in order...]}}"""


def run_judge(case, final):
    import lab  # late import: lab imports this module
    ak = case.get("answer_key") or {}
    rub = ak.get("remediation_rubric", [])
    prompt = judge_prompt(case, final)
    res, obj = None, None
    cost, calls = 0.0, 0
    for attempt in range(2):
        res = lab.call_model(JUDGE_SYSTEM, prompt + ("" if attempt == 0 else "\n\nYour previous reply was not valid JSON. Reply with the JSON object only."),
                             JUDGE_MODEL, timeout=300)
        calls += 1
        cost += res.get("cost_usd", 0.0) or 0.0
        obj, ok = lab.parse_reply(res.get("text", "")) if res.get("ok") else (None, False)
        if ok and isinstance(obj.get("criteria"), list):
            break
        obj = None
    if obj is None:
        return {"ok": False, "error": (res or {}).get("error") or "judge reply unreadable", "cost_usd": round(cost, 6), "calls": calls}
    by_idx = {int(c.get("index", i + 1)): c for i, c in enumerate(obj["criteria"]) if isinstance(c, dict)}
    crit = []
    for i, r in enumerate(rub):
        c = by_idx.get(i + 1, {})
        try:
            pts = float(c.get("points", 0))
        except (TypeError, ValueError):
            pts = 0.0
        crit.append({"criterion": r["criterion"], "max": r["points"], "points": max(0.0, min(float(r["points"]), pts)),
                     "reason": str(c.get("reason", ""))[:400]})
    total, mx = sum(c["points"] for c in crit), sum(c["max"] for c in crit)
    return {"ok": True, "model": res.get("model") or JUDGE_MODEL, "cost_usd": round(cost, 6), "calls": calls,
            "cause_correct": bool(obj.get("cause_correct")), "cause_reason": str(obj.get("cause_reason", ""))[:400],
            "criteria": crit, "points": total, "max_points": mx, "fraction": round(total / mx, 3) if mx else None}


# ------------------------------------------------------------------ evaluation of a run

def evaluate(summary, case, rejudge=True, old=None):
    final = summary.get("final_answer") or {}
    ev = {"version": SCORING_VERSION, "scored_at": time.time(), "case_id": case["id"], "case_hash": summary.get("case", {}).get("hash"),
          "acceptable_fraction": ACCEPTABLE_FRACTION, "has_final_answer": bool(final)}
    if not final:
        ev.update(cause={"pass": False, "groups": []}, evidence={"pass": False, "recall": 0.0, "missing": case["answer_key"].get("required_evidence", [])},
                  remediation={"acceptable": False, "fraction": 0.0, "points": 0, "max_points": sum(r["points"] for r in case["answer_key"]["remediation_rubric"])},
                  judge=None, joint_success=False, flags=["no final answer: " + str(summary.get("end_reason"))])
        return ev
    text = f"{final.get('conclusion') or ''}\n{final.get('plan') or ''}"
    cause = keyword_check(final.get("conclusion") or text, case["answer_key"].get("cause_keywords"))
    evid = evidence_check(final, case)
    judge = run_judge(case, final) if rejudge or not (old or {}).get("judge") else old["judge"]
    flags = []
    if judge.get("ok"):
        rem = {"points": judge["points"], "max_points": judge["max_points"], "fraction": judge["fraction"],
               "acceptable": (judge["fraction"] or 0) >= ACCEPTABLE_FRACTION}
        cause["judge_agrees"] = judge["cause_correct"] == cause["pass"]
        if not cause["judge_agrees"]:
            flags.append("cause: keyword check and judge disagree; needs human review")
    else:
        rem = {"points": None, "max_points": None, "fraction": None, "acceptable": False}
        flags.append("judge failed: " + str(judge.get("error")))
    ev.update(cause=cause, evidence=evid, remediation=rem, judge=judge,
              joint_success=bool(cause["pass"] and evid["pass"] and rem["acceptable"]), flags=flags)
    return ev


def agent_components(summary, trace, case, evaluation):
    """Outcome and accuracy (0-100) per agent for agent score v1. Returns {node_id: {"outcome", "accuracy"}}."""
    nodes = {n["id"]: n for n in summary["network"]["nodes"]}
    coord = next((nid for nid, n in nodes.items() if n["role"] == "coordinator"), None)
    ak = case["answer_key"]
    req = {r.upper() for r in ak.get("required_evidence", [])}
    owner_role = {e["id"].upper(): e["owner"] for e in case.get("evidence", [])}
    valid_ids = set(owner_role)
    corrections = {e["corrects"].upper(): (e["id"].upper(), int(e.get("release_round", 1)))
                   for e in case.get("evidence", []) if e.get("type") == "correction" and e.get("corrects")}
    final = summary.get("final_answer") or {}
    final_round = final.get("round") or 10 ** 6
    final_cited = set((evaluation.get("evidence") or {}).get("cited") or [])
    msgs = [e for e in trace if e["type"] == "message"]
    out = {}
    for nid, n in nodes.items():
        mine = [m for m in msgs if m["from"] == nid]
        cited = [str(x).upper() for m in mine for x in (m.get("evidence_ids") or [])]
        # accuracy: cited IDs must exist; using corrected evidence after its correction was released costs 20 points
        stale = 0
        for m in mine:
            ids = {str(x).upper() for x in (m.get("evidence_ids") or [])}
            for old_id, (corr_id, rnd) in corrections.items():
                if old_id in ids and corr_id not in ids and m["round"] > rnd:
                    stale += 1
        if nid == coord and final:
            for old_id, (corr_id, rnd) in corrections.items():
                if old_id in final_cited and corr_id not in final_cited and final_round > rnd:
                    stale += 1
        if cited or (nid == coord and final_cited):
            pool = cited + (list(final_cited) if nid == coord else [])
            valid = sum(1 for x in pool if x in valid_ids)
            accuracy = max(0.0, 100.0 * valid / len(pool) - 20 * stale)
        else:
            accuracy = None
        # outcome
        if nid == coord:
            c = 1.0 if evaluation["cause"]["pass"] else 0.0
            e = evaluation["evidence"].get("recall") or 0.0
            r = evaluation["remediation"].get("fraction") or 0.0
            outcome = 100.0 * (0.4 * c + 0.3 * e + 0.3 * r)
        else:
            owned = {i for i, role in owner_role.items() if role == n["role"]}
            surfaced = {x for m in mine if m["round"] <= final_round for x in (str(y).upper() for y in (m.get("evidence_ids") or []))}
            owned_req = owned & req
            if owned_req:
                share = len(owned_req & surfaced) / len(owned_req)
                used = 1.0 if owned_req & final_cited else 0.0
                outcome = 100.0 * (0.6 * share + 0.4 * used)
            else:
                useful = {i for i in owned if next((e for e in case["evidence"] if e["id"].upper() == i), {}).get("type") != "distractor"}
                outcome = 100.0 * len(useful & surfaced) / len(useful) if useful else None
        out[nid] = {"outcome": None if outcome is None else round(outcome, 1),
                    "accuracy": None if accuracy is None else round(accuracy, 1), "stale_uses": stale}
    return out


def evaluate_run(rid, rejudge=True):
    """Score a finished case run and store the evaluation + updated agent scores in its run.json."""
    import lab
    d = RUNS / rid
    with LOCK:
        s = _read(d / "run.json")
        if not s or not s.get("case_id") or s.get("status") == "running":
            return None
        case = _read(d / "case.json")
        if not case:  # runs from before case snapshots existed
            import cases
            case = cases.get_case(s["case_id"])
        s["evaluation_status"] = "scoring"
        _write(d / "run.json", s)
    try:
        ev = evaluate(s, case, rejudge=rejudge, old=s.get("evaluation"))
        trace = lab.load_trace(rid)
        comps = agent_components(s, trace, case, ev)
        with LOCK:
            s = _read(d / "run.json")
            s["evaluation"] = ev
            s["evaluation_status"] = "done"
            s["scores"] = lab.score_agents(s, s["agents"], comps)
            _write(d / "run.json", s)
        return ev
    except Exception as e:  # noqa: BLE001
        with LOCK:
            s = _read(d / "run.json")
            s["evaluation_status"] = "error"
            s["evaluation_error"] = f"{type(e).__name__}: {e}"
            _write(d / "run.json", s)
        return None


def evaluate_async(rid):
    threading.Thread(target=evaluate_run, args=(rid,), daemon=True).start()


# ------------------------------------------------------------------ blinded human review

def _scored_case_runs():
    out = []
    for d in RUNS.iterdir():
        s = _read(d / "run.json")
        if s and s.get("evaluation_status") == "done" and (s.get("evaluation") or {}).get("has_final_answer"):
            out.append(s)
    return out


def build_sample(fraction=0.2, minimum=5, seed=None):
    """Draw a random sample of scored case runs for blinded review. Labels are random so they reveal nothing."""
    runs = _scored_case_runs()
    rng = random.Random(seed if seed is not None else time.time())
    k = min(len(runs), max(minimum, round(len(runs) * fraction)))
    chosen = rng.sample(runs, k) if runs else []
    old = _read(REVIEW_FILE, {}) or {}
    items = old.get("items", [])
    have = {i["run_id"] for i in items}
    for s in chosen:
        if s["id"] not in have:
            items.append({"run_id": s["id"], "label": f"Sample {rng.randint(100, 999)}-{len(items) + 1}"})
    rng.shuffle(items)
    sample = {"created": old.get("created") or time.time(), "fraction": fraction, "items": items}
    _write(REVIEW_FILE, sample)
    return sample


def review_queue():
    sample = _read(REVIEW_FILE, {"items": []}) or {"items": []}
    out = []
    for it in sample["items"]:
        s = _read(RUNS / it["run_id"] / "run.json")
        case = _read(RUNS / it["run_id"] / "case.json")
        if not s or not case:
            continue
        fa = s.get("final_answer") or {}
        out.append({"run_id": it["run_id"], "label": it["label"], "reviewed": bool(s.get("human_review")),
                    "case": {"title": case["title"], "brief": case["brief"], "root_cause": case["answer_key"]["root_cause"],
                             "rubric": case["answer_key"]["remediation_rubric"], "required_evidence": case["answer_key"]["required_evidence"]},
                    # deliberately no protocol, network, agent names, model, cost, run id shown in the UI, or judge scores
                    "answer": {"conclusion": fa.get("conclusion"), "plan": fa.get("plan"), "evidence_ids": fa.get("evidence_ids")},
                    "human_review": s.get("human_review")})
    return {"items": out, "created": sample.get("created")}


def submit_review(rid, data, who):
    d = RUNS / rid
    with LOCK:
        s = _read(d / "run.json")
        case = _read(d / "case.json")
        if not s or not case:
            raise KeyError(rid)
        rub = case["answer_key"]["remediation_rubric"]
        pts = [max(0.0, min(float(r["points"]), float(p))) for r, p in zip(rub, data.get("points", []))]
        if len(pts) != len(rub):
            raise ValueError("Score every rubric criterion.")
        mx = sum(r["points"] for r in rub)
        s["human_review"] = {"by": (who or "you")[:60], "at": time.time(), "cause_correct": bool(data.get("cause_correct")),
                             "points": pts, "total": sum(pts), "max_points": mx, "fraction": round(sum(pts) / mx, 3) if mx else None,
                             "acceptable": (sum(pts) / mx if mx else 0) >= ACCEPTABLE_FRACTION, "notes": str(data.get("notes", ""))[:2000]}
        _write(d / "run.json", s)
        return s["human_review"]


def agreement():
    """Judge vs human on the reviewed sample."""
    rows = []
    for s in _scored_case_runs():
        h, ev = s.get("human_review"), s.get("evaluation") or {}
        j = ev.get("judge") or {}
        if not h or not j.get("ok"):
            continue
        rows.append({"run_id": s["id"], "judge_fraction": j.get("fraction"), "human_fraction": h.get("fraction"),
                     "judge_acceptable": ev["remediation"]["acceptable"], "human_acceptable": h["acceptable"],
                     "auto_cause": ev["cause"]["pass"], "human_cause": h["cause_correct"],
                     "per_criterion_abs_diff": [abs(a["points"] - b) for a, b in zip(j.get("criteria", []), h["points"])]})
    n = len(rows)
    if not n:
        return {"n": 0}
    diffs = [abs((r["judge_fraction"] or 0) - (r["human_fraction"] or 0)) for r in rows]
    return {"n": n,
            "acceptable_agreement": round(sum(r["judge_acceptable"] == r["human_acceptable"] for r in rows) / n, 3),
            "cause_agreement": round(sum(r["auto_cause"] == r["human_cause"] for r in rows) / n, 3),
            "mean_abs_fraction_diff": round(sum(diffs) / n, 3),
            "judge_higher": sum((r["judge_fraction"] or 0) > (r["human_fraction"] or 0) for r in rows),
            "human_higher": sum((r["human_fraction"] or 0) > (r["judge_fraction"] or 0) for r in rows),
            "rows": rows}
