"""Pydantic models for the two agents: Financial Assistance Matcher and Appeal Letter Drafter.

Wording rule baked into the shapes: there is NO eligibility-verdict field anywhere. A program can only be
reported as something the patient "may qualify" for, with a source and a next step.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from ..domain.case import Case

EDUCATIONAL_DISCLAIMER = ("Educational information only. This is not medical, legal or financial advice, "
                          "and it cannot tell you whether you qualify for anything.")
HANDOFF = "Talk to a patient navigator, the hospital billing office, or your care team before you act on this."
Mode = Literal["live", "cached"]


# ------------------------------------------------------------------------------ shared
class ToolCallRecord(BaseModel):
    """One row of the 'agent reasoning trail' shown in the UI."""
    step: int = Field(..., ge=1)
    tool: str
    inputs: Dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    source: Optional[str] = Field(None, description="URL or file the output came from, if any")
    ok: bool = True
    error: Optional[str] = None
    duration_ms: int = Field(0, ge=0)
    thought: Optional[str] = Field(None, description="Text the model wrote just before this call (unfiltered; see Explanation.react for the checked version)")


class AgentMeta(BaseModel):
    mode: Mode = "live"
    model: Optional[str] = Field(None, description="Exact model string used; null in cached mode")
    tool_calls_used: int = Field(0, ge=0, le=5)
    trail: List[ToolCallRecord] = Field(default_factory=list)
    disclaimers: List[str] = Field(default_factory=lambda: [EDUCATIONAL_DISCLAIMER])
    handoff: str = HANDOFF
    error: Optional[str] = Field(None, description="Set when the agent stopped early (e.g. tool cap, API failure)")


# ------------------------------------------------------------------------------ explainability (both agents)
class CriterionCheck(BaseModel):
    """One rule of one program, checked against the patient's case. Written by code from the real values used."""
    criterion: str = Field(..., description="e.g. 'Income limit', 'Insurance status', 'Verified source'")
    your_value: str = Field(..., description="What the case says, in plain words")
    program_rule: str = Field(..., description="What the program's published policy requires")
    result: Literal["met", "not_met", "unknown", "not_applicable"]


class ProgramCheck(BaseModel):
    """Why a program is, or is not, in the results. Covers every program in the table, not only the matches."""
    program_name: str
    sponsor: str
    decision: Literal["may_apply", "need_more_info", "ruled_out", "skipped"]
    reason: str = Field(..., description="One plain sentence: the deciding rule")
    criteria: List[CriterionCheck] = Field(default_factory=list)
    source_url: Optional[str] = None


class ExplainStep(BaseModel):
    """One step the agent took, with the reason for it. Reasons come from code, never from a model's self-description."""
    n: int = Field(..., ge=1)
    action: str = Field(..., description="What happened")
    why: str = Field(..., description="Why this step is done")
    result: str = Field(..., description="What it found, with the real numbers")
    who: str = Field(..., description="Who did it: code, or AI (and what checked the AI)")
    source: Optional[str] = None


class ReactStep(BaseModel):
    """One Thought -> Action -> Observation round of the agent (the ReAct pattern).
    The thought is the AI's own sentence if it passed our checks, otherwise a code-written one; `thought_by` says which.
    The observation is always written by code from the tool's real output."""
    n: int = Field(..., ge=1)
    thought: str
    thought_by: Literal["ai", "code"]
    tool: str = Field(..., description="get_case | compute_fpl_percent | lookup_assistance | write_summary")
    action: str = Field(..., description="The action in plain words")
    inputs: Dict[str, Any] = Field(default_factory=dict)
    observation: str = ""
    ok: bool = True
    source: Optional[str] = None
    run_by: Literal["ai", "code"] = Field("ai", description="'ai' = the AI chose to run this tool; 'code' = our code ran it itself")


class Explanation(BaseModel):
    steps: List[ExplainStep] = Field(default_factory=list)
    react: List[ReactStep] = Field(default_factory=list, description="The agent's Thought / Action / Observation trace, in order")
    program_checks: List[ProgramCheck] = Field(default_factory=list)
    not_checked: List[str] = Field(default_factory=list,
                                   description="What the agent did NOT check, or does not know, that could change the answer")


# ------------------------------------------------------------------------------ agent 1: assistance matcher
class FplResult(BaseModel):
    household_size: int = Field(..., ge=1)
    annual_income: float = Field(..., ge=0)
    fpl_percent: float = Field(..., ge=0, description="Income as a percent of the Federal Poverty Level")
    guideline_year: int
    guideline_amount: float = Field(..., gt=0, description="Poverty guideline used, from the data file")
    source_url: str
    data_status: str = Field("UNKNOWN", description="e.g. VERIFIED or FETCHED-NEEDS-HUMAN-CONFIRM, from the data file")


class AssistanceProgram(BaseModel):
    """A row of the curated assistance table (JSON/SQLite). Mirrors the schema you fill from official sources."""
    program_name: str
    sponsor: str
    type: Literal["hospital charity care", "public program", "nonprofit"]
    eligibility_summary: str
    fpl_threshold_pct: Optional[float] = Field(None, ge=0)
    applies_to: Literal["insured", "uninsured", "both"]
    how_to_apply: str
    contact: str
    source_url: str
    retrieved_date: date
    verified: bool = Field(False, description="False = PLACEHOLDER row; must never be shown as fact")


class BillingRightEntry(BaseModel):
    """A row of the curated billing-rights reference. The appeal agent may cite ONLY verified rows."""
    id: str
    topic: str
    plain_summary: str
    citation: str
    source_url: str
    retrieved_date: date
    verified: bool = False


class IncomeNote(BaseModel):
    """Shown when income is above the limit of every income-based program in the table. Written by code from the real
    numbers: it says income-based help is unlikely to apply (never a verdict) and points to other ways to lower the bill."""
    headline: str
    detail: str
    highest_limit_pct: float = Field(..., description="Highest income limit among the programs checked, as % of the poverty level")
    times_poverty_line: float = Field(..., description="Income divided by the poverty line for this household size")
    suggestions: List[str] = Field(default_factory=list)


class AssistanceMatchRequest(BaseModel):
    case: Case
    hospitals: Optional[List[str]] = Field(None, description="Limit hospital-program lookups to these")
    demo_mode: bool = Field(False, description="Return the pre-recorded Sarah output (no API calls)")


class MatchedProgram(BaseModel):
    program_name: str
    sponsor: str
    type: Literal["hospital charity care", "public program", "nonprofit"]
    match_basis: Literal["income_at_or_below_listed_threshold", "no_income_threshold_listed", "need_more_information"]
    why: str = Field(..., description="Plain-language reason it is worth asking about; uses 'may qualify' wording")
    eligibility_summary: str = Field("", description="What the official source says, copied from the curated table")
    contact: str = ""
    source_url: str
    next_step: str
    questions_for_billing_office: List[str] = Field(default_factory=list)
    verified: bool = True


class AssistanceMatchResponse(BaseModel):
    fpl: Optional[FplResult] = None
    matches: List[MatchedProgram] = Field(default_factory=list, description="Ranked, most relevant first")
    not_found: List[str] = Field(default_factory=list,
                                 description="Programs searched with no entry in the table: 'I couldn't find this'")
    summary_plain: str = Field(..., description="About a 6th-grade reading level; no eligibility verdicts")
    income_note: Optional[IncomeNote] = Field(None, description="Set when income is above every income limit in the table")
    explanation: Explanation = Field(default_factory=Explanation,
                                     description="Why each step was taken and why each program is or isn't listed")
    meta: AgentMeta = Field(default_factory=AgentMeta)

    @model_validator(mode="after")
    def _only_verified_programs_shown(self):
        if any(not m.verified for m in self.matches):
            raise ValueError("Unverified (placeholder) programs cannot be returned as matches.")
        return self


# ------------------------------------------------------------------------------ agent 2: appeal drafter
class ParsedDenial(BaseModel):
    """Everything is optional: a field not stated in the text stays null. Never inferred."""
    denial_reason: Optional[str] = None
    denied_service_or_code: Optional[str] = None
    date: Optional[str] = Field(None, description="As written in the letter; not normalised")
    claim_or_reference_number: Optional[str] = None
    appeal_deadline_if_stated: Optional[str] = Field(None, description="As written in the letter")
    plan_name: Optional[str] = None
    missing_documentation_mentioned: Optional[List[str]] = None


class CaseFlag(BaseModel):
    kind: Literal["code_not_in_confirmed_codes", "code_not_stated", "plan_mismatch", "plan_not_stated",
                  "no_confirmed_codes"]
    message: str


class CaseCheck(BaseModel):
    """Result of the plain-code comparison between the parsed denial and the case."""
    denied_code_in_confirmed: Optional[bool] = Field(None, description="None when the letter names no code")
    plan_consistent_with_case: Optional[bool] = Field(None, description="None when either side is unknown")
    flags: List[CaseFlag] = Field(default_factory=list)


class AppealDraftRequest(BaseModel):
    case: Case = Field(..., description="Must include case.denial_text, or pass denial_text below")
    denial_text: Optional[str] = Field(None, max_length=20000, description="Pasted letter, or a short description")
    letter_type: Literal["internal_appeal", "documentation_request"] = "internal_appeal"
    patient_facts: Dict[str, str] = Field(default_factory=dict,
                                          description="Facts the patient typed (e.g. dates). The only facts the letter may use.")
    demo_mode: bool = False

    @model_validator(mode="after")
    def _need_denial_text(self):
        if not (self.denial_text or self.case.denial_text):
            raise ValueError("Paste the denial letter text or describe the denial.")
        return self


class LetterDraft(BaseModel):
    letter_type: Literal["internal_appeal", "documentation_request"]
    body: str = Field(..., description="Draft text with [PLACEHOLDER] fields still to fill in")
    placeholders: List[str] = Field(default_factory=list, description="Each must appear in body, e.g. '[MEMBER ID]'")

    @model_validator(mode="after")
    def _placeholders_present(self):
        missing = [p for p in self.placeholders if p not in self.body]
        if missing:
            raise ValueError(f"Placeholders not found in letter body: {missing}")
        return self


class AppealSendRequest(BaseModel):
    """Sent only after the user approves the draft in the UI. `body` is the letter as the user edited it."""
    to: str = Field(..., max_length=254, description="Typed by the user; never chosen by the AI")
    subject: str = Field(..., max_length=200)
    body: str = Field(..., max_length=20000)
    approved: bool = Field(False, description="Must be true: the user clicked approve")
    draft_id: Optional[str] = Field(None, max_length=200, description="The Gmail draft made earlier; it is updated, then sent")


class GmailDraftRequest(BaseModel):
    """Save the letter as a draft in the patient's own Gmail. Nothing is sent."""
    to: str = Field("", max_length=254, description="Optional; typed by the user")
    subject: str = Field(..., max_length=200)
    body: str = Field(..., max_length=20000)


class GmailDraftResponse(BaseModel):
    draft_id: str
    saved_in: str
    open_url: str = "https://mail.google.com/mail/u/0/#drafts"


class AppealSendResponse(BaseModel):
    sent: bool
    message_id: str
    to: str
    sent_from: str


class AppealDraftResponse(BaseModel):
    denial_summary_plain: str
    parsed: ParsedDenial
    case_check: CaseCheck
    documentation_checklist: List[str] = Field(default_factory=list, description="To request from the care team")
    letter: LetterDraft
    next_steps: List[str] = Field(default_factory=list, description="Always phrased as 'confirm with your plan'")
    explanation: Explanation = Field(default_factory=Explanation, description="Why each step was taken")
    meta: AgentMeta = Field(default_factory=AgentMeta)
