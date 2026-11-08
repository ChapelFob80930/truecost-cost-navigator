"""Deterministic scoring for the extraction eval. No LLM involved."""
from __future__ import annotations

from typing import Dict, Iterable, List


def matches(code: str, token: str) -> bool:
    """ICD families match by prefix ('C50' matches 'C50.911'); everything else matches exactly."""
    code, token = code.upper(), token.upper()
    return code == token or code.startswith(token + ".")


def score_note(key: dict, output: List[dict], raw: Iterable[dict], allowed: Iterable[str]) -> dict:
    """output: post-filtered [{code, confidence}]; raw: model output before the post-filter."""
    codes = [o["code"].upper() for o in output]
    conf = {o["code"].upper(): float(o.get("confidence", 0)) for o in output}

    expected = list(key.get("expected", []))
    groups = list(key.get("expected_groups", []))
    acceptable = list(key.get("acceptable", []))

    tp = sum(1 for e in expected if any(matches(c, e) for c in codes))
    tp += sum(1 for g in groups if any(matches(c, t) for c in codes for t in g))
    expected_n = len(expected) + len(groups)

    def is_ok(c: str) -> bool:
        return (any(matches(c, e) for e in expected) or any(matches(c, t) for g in groups for t in g)
                or any(matches(c, a) for a in acceptable))

    fp_codes = [c for c in codes if not is_ok(c)]
    # an expected token can be hit by several outputs (C50.911 + C50.912): count once as TP, extras are not FPs
    n_out = tp + len(fp_codes)

    must_not_hits = [c for c in codes if any(matches(c, m) for m in key.get("must_not_include", []))]
    cap_violations = [c for cap in key.get("conf_caps", []) for c in codes
                      if any(matches(c, t) for t in cap["codes"]) and conf[c] > cap["max"]]

    allowed_set = {a.upper() for a in allowed}
    raw_codes = [str(r.get("code", "")).upper() for r in raw]
    hallucinated = [c for c in raw_codes if c not in allowed_set]

    return {
        "id": key["id"], "scenario": key["scenario"], "output": codes,
        "tp": tp, "expected": expected_n, "fp": fp_codes,
        "precision": 1.0 if n_out == 0 else tp / n_out,
        "recall": 1.0 if expected_n == 0 else tp / expected_n,
        "must_not_hits": must_not_hits, "conf_cap_violations": cap_violations,
        "raw_count": len(raw_codes), "hallucinated": hallucinated,
    }


def summarize(per_note: List[dict]) -> Dict[str, float]:
    tp = sum(n["tp"] for n in per_note)
    fp = sum(len(n["fp"]) for n in per_note)
    exp = sum(n["expected"] for n in per_note)
    raw = sum(n["raw_count"] for n in per_note)
    return {
        "precision": tp / (tp + fp) if tp + fp else 1.0,
        "recall": tp / exp if exp else 1.0,
        "hallucinated_code_rate": (sum(len(n["hallucinated"]) for n in per_note) / raw) if raw else 0.0,
        "must_not_include_hits": sum(len(n["must_not_hits"]) for n in per_note),
        "conf_cap_violations": sum(len(n["conf_cap_violations"]) for n in per_note),
        "notes_perfect": sum(1 for n in per_note if n["precision"] == 1.0 and n["recall"] == 1.0
                             and not n["must_not_hits"] and not n["conf_cap_violations"]),
        "notes_total": len(per_note),
    }
