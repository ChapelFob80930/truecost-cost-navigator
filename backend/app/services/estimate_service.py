"""EstimateService: ranks hospitals by what the patient would pay, and simulates the care journey.

Money math is delegated to InsuredCostCalculator. Rates come from PlanTypeSelector (median within the chosen plan type).
Inpatient (MS-DRG) and percent-of-charges rates are never priced: they go to `unavailable`.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Dict, List, Optional, Sequence, Tuple

from ..domain.calculators import InsuredCostCalculator, money
from ..domain.geo import GeoService
from ..domain.models import Hospital, InsuranceProfile, MedicalCode, Payer, PriceRecord
from ..domain.plan_types import TYPES, available_types, parse_selection
from ..domain.rate_selector import PlanTypeSelector
from ..exceptions import InvalidPlanError, NoBillableCodesError, UnknownCodeError, UnknownHospitalError
from ..repositories.base import PriceRepository
from ..schemas import (EstimateRequest, EstimateResponse, HospitalResult, LineOut, PathwayStep,
                       SimulateRequest, SimulateResponse, TimelineEntry, UnavailableOut)

CAVEATS = [
    "Estimates only - not a bill or a guarantee of coverage.",
    "Facility fees only. Doctor, radiologist and pathologist fees are billed separately.",
    "Based on hospital price-transparency files; rates can change and may contain errors.",
    "Built with synthetic test scenarios only - no real patient data is used or stored.",
    "Assumes a coinsurance-based plan. Flat copays are not modelled.",
    "Inpatient stays are priced by formula: estimate unavailable, contact hospital.",
    "Drive times are rough estimates from ZIP code, not routed directions.",
]
INPATIENT_MSG = "estimate unavailable, contact hospital"
INSURERS = {p.value for p in Payer}
ESTIMATE_CAVEAT = ("Lines marked 'estimated' are not published by that hospital for your insurer. They are the "
                   "middle of the other insurers' published rates at the same hospital, so treat them as a rough guide "
                   "and confirm with the hospital.")
DEFAULT_REPEAT = {"96413": 6}  # illustrative: 6 chemo cycles when no pathway is given


@dataclass
class _Priced:
    code: str
    description: str
    rate: Decimal
    rule: str
    estimated: bool = False
    note: Optional[str] = None


class EstimateService:
    def __init__(self, repo: PriceRepository, selector: PlanTypeSelector, geo: GeoService):
        self._repo = repo
        self._selector = selector
        self._geo = geo

    # ------------------------------------------------------------------ /estimate
    def estimate(self, req: EstimateRequest) -> EstimateResponse:
        known, profile = self._prepare(req)
        index = self._index(req.codes, profile)
        origin = self._geo.zip_to_latlng(req.patient_zip)
        results = [self._hospital_result(h, req.codes, known, index, profile, origin, req.include_estimates)
                   for h in self._repo.list_hospitals()]

        comparable = [r for r in results if r.complete]
        if comparable:
            worst = max(r.patient_oop for r in comparable)
            for r in comparable:
                r.savings_vs_highest = round(worst - r.patient_oop, 2)
        # comparable hospitals first (cheapest first), then partially priced ones
        results.sort(key=lambda r: (not r.complete, r.patient_oop, r.total_allowed, r.hospital))

        caveats = list(CAVEATS)
        if req.patient_zip and origin is None:
            caveats.append(f"ZIP {req.patient_zip} is not in our WNY table, so drive times are unavailable.")
        if results and not comparable:
            caveats.append("No hospital has a published rate for every selected code, so none can be ranked.")
        elif any(not r.complete for r in results):
            caveats.append("Hospitals marked incomplete lack a published rate for some codes and are not ranked.")
        if any(r.has_estimates for r in results):
            caveats.append(ESTIMATE_CAVEAT)
        return EstimateResponse(results=results, caveats=caveats)

    # ------------------------------------------------------------------ /simulate
    def simulate(self, req: SimulateRequest) -> SimulateResponse:
        known, profile = self._prepare(req)
        index = self._index(req.codes, profile)
        names = {h.name: h for h in self._repo.list_hospitals()}
        hospital = self._pick_hospital(req, known, index, profile, names)

        pathway = req.pathway or self._default_pathway(req.codes, known)
        for step in pathway:
            if step.code not in known:
                known.update({c.code: c for c in self._repo.get_codes([step.code])})
            if step.code not in known:
                raise UnknownCodeError([step.code])
        # a diagnosis (ICD-10) says why care is happening; it has no price, so it is not a step on the cost timeline
        pathway = [s for s in pathway if known[s.code].code_type != "ICD10"]
        if not pathway:
            raise NoBillableCodesError()
        if any(s.code not in req.codes for s in pathway):
            index = self._index([s.code for s in pathway] + list(req.codes), profile)

        # expand repeats into dated occurrences, keeping given order within a month
        occurrences: List[Tuple[int, int, PathwayStep]] = []
        for pos, s in enumerate(pathway):
            for i in range(s.repeat):
                occurrences.append((s.month + i, pos, s))
        occurrences.sort(key=lambda o: (o[0], o[1]))

        priced: List[Tuple[int, PathwayStep, _Priced]] = []
        unavailable: Dict[str, str] = {}
        for month, _, s in occurrences:
            line, reason = self._price(known[s.code], index.get((hospital.name, s.code), []), profile,
                                       req.include_estimates)
            if line:
                priced.append((month, s, line))
            else:
                unavailable[s.code] = reason

        costs = InsuredCostCalculator(profile).calculate([p.rate for _, _, p in priced])
        timeline: List[TimelineEntry] = []
        cumulative = Decimal("0")
        reached: Optional[int] = None
        for (month, s, p), c in zip(priced, costs):
            cumulative += c.patient_pays
            timeline.append(TimelineEntry(month=month, step=s.step or p.description, code=s.code,
                                          cost=float(c.patient_pays), cumulative_oop=float(cumulative)))
            if reached is None and cumulative >= profile.oop_max_remaining:
                reached = month
        caveats = list(CAVEATS)
        if any(p.estimated for _, _, p in priced):
            caveats.append(ESTIMATE_CAVEAT)
        return SimulateResponse(hospital=hospital.name, timeline=timeline, oop_max_reached_month=reached,
                                unavailable=[UnavailableOut(code=k, reason=v) for k, v in unavailable.items()],
                                caveats=caveats)

    # ------------------------------------------------------------------ helpers
    def _prepare(self, req: EstimateRequest) -> Tuple[Dict[str, MedicalCode], InsuranceProfile]:
        known = {c.code: c for c in self._repo.get_codes(req.codes)}
        missing = [c for c in req.codes if c not in known]
        if missing:
            raise UnknownCodeError(missing)
        # ICD-10 diagnosis codes describe the condition; they have no facility price, so they are not priced.
        req.codes[:] = [c for c in req.codes if known[c].code_type != "ICD10"]
        if not req.codes:
            raise NoBillableCodesError()
        i = req.insurance
        types = available_types(self._repo.list_plans(i.insurer.value))
        wanted = parse_selection(i.plan)
        if wanted is None or not any(t in wanted for t in types):
            raise InvalidPlanError(i.insurer.value, i.plan, types)
        profile = InsuranceProfile(
            payer=i.insurer, plan=i.plan,
            deductible_remaining=Decimal(str(i.deductible_remaining)),
            coinsurance_pct=Decimal(str(i.coinsurance_pct)),
            oop_max_remaining=Decimal(str(i.oop_max_remaining)))
        return known, profile

    def _index(self, codes: Sequence[str], profile: InsuranceProfile) -> Dict[Tuple[str, str], List[PriceRecord]]:
        index: Dict[Tuple[str, str], List[PriceRecord]] = defaultdict(list)
        # all payers: the other insurers' rows are only used to label-and-estimate a missing rate
        for r in self._repo.find_prices(list(dict.fromkeys(codes)), None):
            index[(r.hospital, r.code)].append(r)
        return index

    def _estimate(self, code: MedicalCode, recs: List[PriceRecord],
                  profile: InsuranceProfile) -> Optional[_Priced]:
        """Rate for an insurer the hospital did not publish: the median of the OTHER insurers' published rates
        at this same hospital (each insurer reduced to its own median first, same plan type if possible)."""
        mine = profile.payer.value
        wanted = parse_selection(profile.plan or "") or set()
        by_payer: Dict[str, List[PriceRecord]] = defaultdict(list)
        for r in recs:
            if r.payer and r.payer != mine and r.payer in INSURERS and r.is_dollar_rate:
                by_payer[r.payer].append(r)
        for widen in (False, True):  # same plan type first; only then any plan type, and say so
            picks = {}
            for payer, rows in by_payer.items():
                sel = self._selector.select(rows, set(TYPES) if widen else wanted)
                if sel:
                    picks[payer] = sel.rate
            if picks:
                break
        else:
            return None
        rate = money(median(picks.values()))
        names = " and ".join(sorted(picks))
        spread = f" (${money(min(picks.values()))}-${money(max(picks.values()))})" if len(picks) > 1 else ""
        note = (f"Not published for {mine}. Estimated from {names}'s published rate{'s' if len(picks) > 1 else ''} "
                f"at this hospital{spread}" + ("; plan type not matched" if widen else "") + ".")
        return _Priced(code.code, code.description, rate, f"Estimated, not published: {note}", True, note)

    def _price(self, code: MedicalCode, recs: List[PriceRecord],
               profile: InsuranceProfile, allow_estimate: bool = False) -> Tuple[Optional[_Priced], str]:
        if code.code_type == "MS-DRG":
            return None, INPATIENT_MSG
        payer_recs = [r for r in recs if r.payer == profile.payer.value]
        own = [r for r in recs if r.payer is None or r.payer == profile.payer.value]
        sel = self._selector.select(payer_recs, parse_selection(profile.plan or "") or set())
        if sel:
            rule = f"Negotiated rate, {profile.payer.value} {profile.plan} plans"
            if sel.candidates > 1:
                rule += f" (median of {sel.candidates} plan rates, ${money(sel.low)}-${money(sel.high)})"
            return _Priced(code.code, code.description, sel.rate, rule), ""
        if any(r.rate_type == "percent_of_charges" for r in payer_recs):
            return None, f"{profile.payer.value} rate is a percent of charges, not a dollar amount: {INPATIENT_MSG}"
        if payer_recs:
            return None, f"No dollar rate published for {profile.plan} plans"
        if allow_estimate:  # the insurer has no row here at all -> labelled estimate, never silently
            est = self._estimate(code, recs, profile)
            if est:
                return est, ""
        if own:
            return None, f"No {profile.payer.value} rate published - gross charge only"
        return None, "Not found in this hospital's price file"

    def _hospital_result(self, h: Hospital, codes: Sequence[str], known: Dict[str, MedicalCode],
                         index: Dict[Tuple[str, str], List[PriceRecord]], profile: InsuranceProfile,
                         origin: Optional[Tuple[float, float]], allow_estimate: bool = True) -> HospitalResult:
        priced: List[_Priced] = []
        unavailable: List[UnavailableOut] = []
        outpatient_missing = False
        for code in codes:
            line, reason = self._price(known[code], index.get((h.name, code), []), profile, allow_estimate)
            if line:
                priced.append(line)
            else:
                unavailable.append(UnavailableOut(code=code, reason=reason))
                if known[code].code_type != "MS-DRG":
                    outpatient_missing = True
        costs = InsuredCostCalculator(profile).calculate([p.rate for p in priced])
        return HospitalResult(
            hospital=h.name, address=h.address, lat=h.lat, lng=h.lng,
            complete=not outpatient_missing and bool(priced),
            has_estimates=any(p.estimated for p in priced),
            total_allowed=float(sum((c.allowed for c in costs), Decimal("0"))),
            patient_oop=float(sum((c.patient_pays for c in costs), Decimal("0"))),
            drive_minutes=self._geo.drive_minutes(origin, h.lat, h.lng),
            lines=[LineOut(code=p.code, description=p.description, rate=float(c.allowed),
                           patient_pays=float(c.patient_pays), rule=p.rule,
                           price_source="estimated" if p.estimated else "published", estimate_note=p.note)
                   for p, c in zip(priced, costs)],
            unavailable=unavailable)

    def _pick_hospital(self, req: SimulateRequest, known, index, profile, names) -> Hospital:
        if req.hospital:
            if req.hospital not in names:
                raise UnknownHospitalError(req.hospital, list(names))
            return names[req.hospital]
        results = [self._hospital_result(h, req.codes, known, index, profile, None, req.include_estimates)
                   for h in names.values()]
        usable = sorted((r for r in results if r.complete), key=lambda r: (r.patient_oop, r.hospital))
        if not usable:  # nothing fully priced: fall back to whoever prices the most codes
            usable = sorted(results, key=lambda r: (-len(r.lines), r.patient_oop, r.hospital))
        return names[usable[0].hospital]

    @staticmethod
    def _default_pathway(codes: Sequence[str], known: Dict[str, MedicalCode]) -> List[PathwayStep]:
        """Diagnostic codes in month 1; each treatment code gets its own following month."""
        steps, month = [], 1
        for code in codes:
            if known[code].phase == "treatment":
                month += 1
            steps.append(PathwayStep(code=code, month=month, repeat=DEFAULT_REPEAT.get(code, 1)))
        return steps
