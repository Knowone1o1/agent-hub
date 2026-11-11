"""
Test cases for the Agent Lab: disruptions/incidents with staged, role-owned evidence and a hidden answer key.

A case file (data/lab/cases/<id>.json):
  id, title, domain (role pack key), split ("dev" | "heldout"), difficulty, brief (shared with every agent)
  evidence: [{id, owner (role key), release_round, type: clue|distractor|correction|context, source, content, corrects?}]
  answer_key: {root_cause, cause_keywords: [[synonyms...], ...] (every group must match), required_evidence: [ids],
               supporting_evidence: [ids], remediation_rubric: [{criterion, points}], notes}
  registry: {features: [...], mechanism_opportunities, expected_timing, favoritism_notes}
  workflow: status draft -> reviewed -> frozen; author, reviewed_by, approved_by, frozen_at, history[]

Agents never see the answer key or the evidence "type" (clue/distractor); they only see source and content.
Frozen cases cannot be edited; unfreezing requires a reason and is recorded in the history (proposal: disclose changes).
"""

import hashlib
import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "data" / "lab" / "cases"
CASES.mkdir(parents=True, exist_ok=True)

EVIDENCE_TYPES = ("clue", "distractor", "correction", "context")
FEATURES = {
    "relay_heavy": "Relay-heavy evidence: findings that other roles need repeated across roles",
    "low_use_detail": "Low-use detail: a verbose finding others may never need",
    "unknown_downstream_need": "Unknown downstream need: the recipient can't predict which evidence matters",
    "late_correction": "Late evidence correction: an earlier credible clue is later contradicted",
    "cross_specialist_dependency": "Cross-specialist dependency: the cause needs two or more evidence domains",
    "negative_control": "Negative control: little forwarding, little unused detail, no real contradiction",
}


def _read(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write(p, obj):
    import os, uuid
    tmp = Path(p).with_name(Path(p).name + f".{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    for i in range(40):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(0.025 * (i + 1))
    raise PermissionError(str(p))


def _safe(cid):
    if not re.fullmatch(r"[a-z0-9-]{3,60}", cid or ""):
        raise KeyError(cid)
    return cid


def case_hash(c):
    """Fingerprint of everything that affects a run or its scoring (not the workflow fields)."""
    core = {k: c.get(k) for k in ("domain", "brief", "evidence", "answer_key")}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:12]


def list_cases(domain=None):
    out = []
    for f in sorted(CASES.glob("*.json")):
        c = _read(f)
        if c and (not domain or c.get("domain") == domain):
            out.append(c)
    order = {"dev": 0, "heldout": 1}
    return sorted(out, key=lambda c: (c.get("domain", ""), order.get(c.get("split"), 2), c["id"]))


def get_case(cid):
    c = _read(CASES / f"{_safe(cid)}.json")
    if not c:
        raise KeyError(cid)
    c["hash"] = case_hash(c)
    return c


def validate(c, role_keys):
    errs = []
    if not c.get("title"):
        errs.append("Title is required.")
    if c.get("split") not in ("dev", "heldout"):
        errs.append("Split must be dev or heldout.")
    if not (c.get("brief") or "").strip():
        errs.append("The shared brief is required.")
    ids = set()
    for e in c.get("evidence", []):
        eid = str(e.get("id", "")).strip()
        if not re.fullmatch(r"[A-Za-z]{1,4}-?\d{1,3}", eid):
            errs.append(f"Evidence ID '{eid}' should look like E1 or SC-7.")
        if eid in ids:
            errs.append(f"Evidence ID {eid} is used twice.")
        ids.add(eid)
        if e.get("owner") not in role_keys:
            errs.append(f"{eid}: owner '{e.get('owner')}' is not a role in this domain.")
        if e.get("type") not in EVIDENCE_TYPES:
            errs.append(f"{eid}: type must be one of {', '.join(EVIDENCE_TYPES)}.")
        try:
            if not 1 <= int(e.get("release_round", 1)) <= 20:
                raise ValueError
        except (TypeError, ValueError):
            errs.append(f"{eid}: release round must be 1-20.")
        if e.get("type") == "correction" and e.get("corrects") not in ids | {x.get("id") for x in c.get("evidence", [])}:
            errs.append(f"{eid}: a correction must name the evidence ID it corrects.")
        if not (e.get("content") or "").strip():
            errs.append(f"{eid}: content is empty.")
    ak = c.get("answer_key") or {}
    if not (ak.get("root_cause") or "").strip():
        errs.append("Answer key: root cause is required.")
    for rid in ak.get("required_evidence", []):
        if rid not in ids:
            errs.append(f"Answer key: required evidence {rid} doesn't exist.")
    if not ak.get("required_evidence"):
        errs.append("Answer key: list at least one required evidence ID.")
    if not ak.get("remediation_rubric"):
        errs.append("Answer key: add at least one remediation rubric criterion.")
    for f in (c.get("registry") or {}).get("features", []):
        if f not in FEATURES:
            errs.append(f"Unknown registry feature '{f}'.")
    return errs


def save_case(data, role_keys_for, editor="you"):
    c = dict(data)
    if not c.get("id"):
        base = re.sub(r"[^a-z0-9]+", "-", (c.get("title") or "case").lower()).strip("-")[:40] or "case"
        cid, n = base, 2
        while (CASES / f"{cid}.json").exists():
            cid, n = f"{base}-{n}", n + 1
        c["id"] = cid
    old = _read(CASES / f"{_safe(c['id'])}.json")
    if not old:
        c.update(status="draft", author=c.get("author") or editor, created=time.time(),
                 history=[{"t": time.time(), "event": "created", "by": c.get("author") or editor}])
    if old and old.get("status") == "frozen":
        raise ValueError("This case is frozen. Unfreeze it (with a reason) before editing.")
    errs = validate(c, role_keys_for(c.get("domain")))
    if errs:
        raise ValueError(" ".join(errs[:6]) + (f" (+{len(errs) - 6} more)" if len(errs) > 6 else ""))
    if old:
        for k in ("status", "author", "reviewed_by", "approved_by", "frozen_at", "history", "created"):
            if k in old:
                c[k] = old[k]
        if c.get("status") == "reviewed" and case_hash(old) != case_hash(c):
            c["status"] = "draft"
            c["reviewed_by"] = None
            c["history"].append({"t": time.time(), "event": "edited after review, back to draft", "by": editor})
        else:
            c["history"].append({"t": time.time(), "event": "edited", "by": editor})
    c["updated"] = time.time()
    c.pop("hash", None)
    _write(CASES / f"{c['id']}.json", c)
    c["hash"] = case_hash(c)
    return c


def transition(cid, action, who, reason=""):
    c = get_case(cid)
    c.pop("hash", None)
    who = (who or "").strip()[:60]
    if not who:
        raise ValueError("Enter your name for the record.")
    st = c.get("status", "draft")
    now = time.time()
    if action == "review":
        if st != "draft":
            raise ValueError("Only draft cases can be marked reviewed.")
        c.update(status="reviewed", reviewed_by=who)
    elif action == "freeze":
        if st != "reviewed":
            raise ValueError("Review the case before freezing it.")
        c.update(status="frozen", approved_by=who, frozen_at=now)
    elif action == "unfreeze":
        if st != "frozen":
            raise ValueError("The case isn't frozen.")
        if len(reason.strip()) < 5:
            raise ValueError("Give a reason for unfreezing; it is kept in the case history.")
        c.update(status="draft", reviewed_by=None, approved_by=None, frozen_at=None)
    elif action == "reopen":
        if st != "reviewed":
            raise ValueError("Only reviewed cases can be reopened.")
        c.update(status="draft", reviewed_by=None)
    else:
        raise ValueError("Unknown action.")
    c.setdefault("history", []).append({"t": now, "event": action, "by": who, **({"reason": reason.strip()} if reason else {}),
                                        "hash": case_hash(c)})
    _write(CASES / f"{cid}.json", c)
    c["hash"] = case_hash(c)
    return c


def delete_case(cid):
    c = get_case(cid)
    if c.get("status") == "frozen":
        raise ValueError("Frozen cases can't be deleted. Unfreeze first.")
    (CASES / f"{cid}.json").unlink(missing_ok=True)


def public_view(c, include_key=True):
    """The case as shown in the editor. include_key=False hides the answer key (for blinded screens)."""
    out = dict(c)
    if not include_key:
        out.pop("answer_key", None)
        out["evidence"] = [{k: v for k, v in e.items() if k != "type"} for e in c.get("evidence", [])]
    return out


def registry(domain=None):
    rows = []
    for c in list_cases(domain):
        ev = c.get("evidence", [])
        rows.append({"id": c["id"], "title": c["title"], "domain": c.get("domain"), "split": c.get("split"),
                     "status": c.get("status"), "difficulty": c.get("difficulty"), "author": c.get("author"),
                     "reviewed_by": c.get("reviewed_by"), "approved_by": c.get("approved_by"),
                     "features": (c.get("registry") or {}).get("features", []),
                     "evidence": len(ev), "distractors": sum(1 for e in ev if e.get("type") == "distractor"),
                     "corrections": sum(1 for e in ev if e.get("type") == "correction"),
                     "owners": sorted({e.get("owner") for e in ev}),
                     "last_release_round": max([int(e.get("release_round", 1)) for e in ev] or [0]),
                     "required_evidence": (c.get("answer_key") or {}).get("required_evidence", []),
                     "hash": case_hash(c)})
    return {"cases": rows, "features": FEATURES}
