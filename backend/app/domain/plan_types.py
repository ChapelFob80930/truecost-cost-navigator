"""Plan TYPES. Price files list dozens of hospital-specific plan names ("Article 44 HMO", "Choice Plus",
"Healthnow PPO/Traditional"), and each name exists at only some hospitals. To compare hospitals fairly
we group names into types and take the median rate within the chosen type at each hospital.

Classification is keyword-based and multi-label ("Healthnow HMO/POS" is both HMO and POS).
Names with no keyword become "Other". The patient may also choose "Any" (every commercial plan).
A request like "HMO/PPO" means HMO or PPO.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Set

TYPES = ["HMO", "PPO", "POS", "Other"]
ANY = "Any"


def classify(plan_name: Optional[str]) -> Set[str]:
    name = (plan_name or "").upper()
    found = {t for t in ("HMO", "PPO", "POS") if re.search(rf"\b{t}\b", name)}
    # NY product names that carry no HMO/PPO word. Kaleida's own file labels them "Article 43 PPO" and
    # "Article 44 HMO" (NY Insurance Law Art. 43 = indemnity/PPO products, Public Health Law Art. 44 = HMOs),
    # while the Catholic Health files list the same products as just "IHA Article 43" / "Commercial IHA 44".
    if re.search(r"\b(?:ARTICLE|IHA) 43\b", name):
        found.add("PPO")
    if re.search(r"\b(?:ARTICLE|IHA) 44\b", name):
        found.add("HMO")
    return found or {"Other"}


def parse_selection(selection: str) -> Optional[Set[str]]:
    """'HMO' -> {HMO}; 'HMO/PPO' -> {HMO, PPO}; 'Any' -> set(TYPES). None if any token is unknown."""
    tokens = [t.strip() for t in re.split(r"[/,]", selection) if t.strip()]
    lookup = {t.upper(): t for t in TYPES + [ANY]}
    if not tokens or any(t.upper() not in lookup for t in tokens):
        return None
    chosen = {lookup[t.upper()] for t in tokens}
    return set(TYPES) if ANY in chosen else chosen


def plan_matches(plan_name: Optional[str], wanted: Set[str]) -> bool:
    return bool(classify(plan_name) & wanted)


def available_types(plan_names: Iterable[str]) -> List[str]:
    present: Set[str] = set()
    for p in plan_names:
        present |= classify(p)
    return [t for t in TYPES if t in present] + ([ANY] if present else [])
