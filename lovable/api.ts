// src/lib/api.ts  (paste into the Lovable project)
// One place for the backend URL and every call. Change API_BASE when you deploy.
export const API_BASE = "https://YOUR-BACKEND-URL"; // must be https (Lovable pages are https); no trailing slash

// ---------- shared types ----------
export type Insurer = "Independent Health" | "Highmark BCBS" | "Univera";
export type PlanType = "HMO" | "PPO" | "POS" | "Other" | "Any"; // from GET /api/v1/plans?insurer=... (or a combo like "HMO/PPO")

export interface Insurance {
  insurer: Insurer;
  plan: string; // a plan TYPE, not a plan name
  deductible_remaining: number;
  coinsurance_pct: number; // 0-100
  oop_max_remaining: number;
}

export interface ExtractedCode { code: string; type: "CPT" | "ICD10"; description: string; confidence: number; evidence: string }
export interface ExtractResponse { codes: ExtractedCode[]; extractor: string }
export interface ExtractFileResponse extends ExtractResponse {
  note_text: string;   // the text read from the file: show it in an editable box so the patient can fix OCR mistakes
  read_method: "text" | "docx" | "pdf-text" | "gemini-ocr";
  file_type: "pdf" | "png" | "jpeg" | "webp" | "docx" | "txt";
  truncated: boolean;  // note was longer than 20,000 characters and was cut
}

export interface EstimateLine {
  code: string; description: string | null; rate: number; patient_pays: number; rule: string;
  price_source: "published" | "estimated"; // "estimated" = hospital did not publish this insurer's rate; borrowed from other insurers
  estimate_note: string | null;            // show next to estimated lines
}
export interface Unavailable { code: string; reason: string }
export interface HospitalResult {
  hospital: string; address: string | null; lat: number | null; lng: number | null;
  complete: boolean; // false = missing a price for some code: show under "can't be compared", never rank it
  has_estimates: boolean; // true = at least one line is an estimate: show an "Includes estimates" badge
  total_allowed: number; patient_oop: number; savings_vs_highest: number | null; drive_minutes: number | null;
  lines: EstimateLine[]; unavailable: Unavailable[];
}
export interface EstimateResponse { results: HospitalResult[]; caveats: string[] }

export interface PathwayStep { code: string; month: number; step?: string; repeat?: number }
export interface TimelineEntry { month: number; step: string; code: string; cost: number; cumulative_oop: number }
export interface SimulateResponse { hospital: string; timeline: TimelineEntry[]; oop_max_reached_month: number | null; unavailable: Unavailable[]; caveats: string[] }

// ---------- assistant (agents) ----------
export interface CaseObject {
  confirmed_codes?: string[];
  uninsured?: boolean;
  insurer?: Insurer; plan?: string; deductible_remaining?: number; coinsurance_pct?: number; oop_max_remaining?: number;
  estimates?: { hospital: string; patient_oop: number; total_allowed: number; complete: boolean }[];
  household_size?: number; annual_income?: number;
  denial_text?: string;
}
export interface ToolCallRecord { step: number; tool: string; inputs: Record<string, unknown>; output: unknown; source: string | null; ok: boolean; error: string | null; duration_ms: number }
export interface AgentMeta { mode: "live" | "cached"; model: string | null; tool_calls_used: number; trail: ToolCallRecord[]; disclaimers: string[]; handoff: string; error: string | null }
export interface FplResult { household_size: number; annual_income: number; fpl_percent: number; guideline_year: number; guideline_amount: number; source_url: string; data_status: string }
export interface MatchedProgram {
  program_name: string; sponsor: string; type: string;
  match_basis: "income_at_or_below_listed_threshold" | "no_income_threshold_listed" | "need_more_information";
  why: string; eligibility_summary: string; contact: string; source_url: string; next_step: string; questions_for_billing_office: string[];
}
// Explainable AI: reasons are written by code from the real values used (never the model's own account of itself)
export interface CriterionCheck { criterion: string; your_value: string; program_rule: string; result: "met" | "not_met" | "unknown" | "not_applicable" }
export interface ProgramCheck { program_name: string; sponsor: string; decision: "may_apply" | "need_more_info" | "ruled_out" | "skipped"; reason: string; criteria: CriterionCheck[]; source_url: string | null }
export interface ExplainStep { n: number; action: string; why: string; result: string; who: string; source: string | null }
// ReAct trace: Thought -> Action -> Observation. thought_by "ai" = the AI's own sentence (shown only after our checks);
// "code" = written by our code. The observation is always written by code from the tool's real result.
export interface ReactStep { n: number; thought: string; thought_by: "ai" | "code"; tool: string; action: string; inputs: Record<string, unknown>; observation: string; ok: boolean; source: string | null; run_by: "ai" | "code" }
export interface Explanation { steps: ExplainStep[]; react: ReactStep[]; program_checks: ProgramCheck[]; not_checked: string[] }
// Set when income is above the limit of every income-based program: show it as a prominent card with the suggestions
export interface IncomeNote { headline: string; detail: string; highest_limit_pct: number; times_poverty_line: number; suggestions: string[] }
export interface AssistanceResponse { fpl: FplResult | null; matches: MatchedProgram[]; not_found: string[]; summary_plain: string; income_note: IncomeNote | null; explanation: Explanation; meta: AgentMeta }

export interface ParsedDenial { denial_reason: string | null; denied_service_or_code: string | null; date: string | null; claim_or_reference_number: string | null; appeal_deadline_if_stated: string | null; plan_name: string | null; missing_documentation_mentioned: string[] | null }
export interface CaseFlag { kind: string; message: string }
export interface AppealResponse {
  denial_summary_plain: string; parsed: ParsedDenial;
  case_check: { denied_code_in_confirmed: boolean | null; plan_consistent_with_case: boolean | null; flags: CaseFlag[] };
  documentation_checklist: string[]; letter: { letter_type: string; body: string; placeholders: string[] }; next_steps: string[]; explanation: Explanation; meta: AgentMeta;
}

// ---------- live usage (time-series from Tiger Data) ----------
export interface UsageStats {
  backend: "tiger" | "memory"; time_series: boolean; window_minutes: number; bucket_minutes: number;
  totals: { requests: number; errors: number; p50_ms: number | null; p95_ms: number | null; ai_live: number; ai_cached: number };
  by_endpoint: { endpoint: string; requests: number; avg_ms: number }[];
  series: { bucket: string; requests: number; errors: number; avg_ms: number | null }[]; // one point per bucket, zeros included
  last_event_at: string | null;
}

// Live events from POST /api/v1/assistance/match/stream (Server-Sent Events)
export type AssistanceStreamEvent =
  | { type: "start"; mode: "live" | "cached" }
  | { type: "step_start"; step: ReactStep }                       // a Thought and an Action, observation still empty
  | { type: "step_done"; n: number; observation: string; ok: boolean } // fills in that step's Observation
  | { type: "final"; response: AssistanceResponse }
  | { type: "error"; detail: string };

// ---------- fetch helper ----------
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    // "ngrok-skip-browser-warning" stops ngrok's free-plan interstitial page from replacing the API's JSON. Harmless elsewhere.
    res = await fetch(`${API_BASE}${path}`, { headers: { "Content-Type": "application/json", "ngrok-skip-browser-warning": "true" }, ...init });
  } catch {
    throw new Error("Can't reach the TrueCost server. Check your connection, or turn on demo mode.");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const d = body.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x: { msg?: string }) => x.msg).join("; ") : `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}
const post = <T,>(path: string, body: unknown) => call<T>(path, { method: "POST", body: JSON.stringify(body) });

// ---------- calls ----------
export const api = {
  // Runs the assistant and calls onEvent for each live event. Resolves with the final response.
  assistanceMatchStream: async (c: CaseObject, onEvent: (e: AssistanceStreamEvent) => void, demo_mode = false, hospitals?: string[]): Promise<AssistanceResponse> => {
    let res: Response;
    try {
      res = await fetch(`${API_BASE}/api/v1/assistance/match/stream`, { method: "POST", headers: { "Content-Type": "application/json", "ngrok-skip-browser-warning": "true" }, body: JSON.stringify({ case: c, demo_mode, hospitals }) });
    } catch { throw new Error("Can't reach the TrueCost server. Check your connection, or turn on demo mode."); }
    if (!res.ok || !res.body) throw new Error(`Request failed (${res.status})`);
    const reader = res.body.getReader(); const decoder = new TextDecoder();
    let buf = ""; let final: AssistanceResponse | null = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let i: number;
      while ((i = buf.indexOf("\n\n")) >= 0) {           // one SSE message per blank line
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
        if (!line.startsWith("data:")) continue;
        const ev = JSON.parse(line.slice(5)) as AssistanceStreamEvent;
        onEvent(ev);
        if (ev.type === "final") final = ev.response;
        if (ev.type === "error") throw new Error(ev.detail);
      }
    }
    if (!final) throw new Error("The assistant stopped before finishing. Please try again.");
    return final;
  },
  stats: (window_minutes = 60, bucket_minutes = 5) => call<UsageStats>(`/api/v1/stats?window_minutes=${window_minutes}&bucket_minutes=${bucket_minutes}`),
  plans: (insurer: Insurer) => call<string[]>(`/api/v1/plans?insurer=${encodeURIComponent(insurer)}`),
  codes: () => call<{ code: string; code_type: string; description: string; phase: string }[]>("/api/v1/codes"),
  extract: (note: string) => post<ExtractResponse>("/extract", { note }),
  // Upload a doctor's note (PDF, PNG, JPEG, WEBP, DOCX or TXT, max 10 MB). Do NOT set Content-Type: the browser adds the multipart boundary.
  extractFile: async (file: File): Promise<ExtractFileResponse> => {
    const form = new FormData();
    form.append("file", file);
    let res: Response;
    try { res = await fetch(`${API_BASE}/extract/file`, { method: "POST", body: form, headers: { "ngrok-skip-browser-warning": "true" } }); }
    catch { throw new Error("Can't reach the TrueCost server. Check your connection, or paste the note text instead."); }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : `Upload failed (${res.status})`);
    }
    return res.json() as Promise<ExtractFileResponse>;
  },
  estimate: (codes: string[], insurance: Insurance, patient_zip?: string, include_estimates = true) =>
    post<EstimateResponse>("/estimate", { codes, insurance, patient_zip, include_estimates }),
  simulate: (p: { codes: string[]; insurance: Insurance; patient_zip?: string; hospital?: string; pathway?: PathwayStep[]; include_estimates?: boolean }) =>
    post<SimulateResponse>("/simulate", p),
  assistanceMatch: (c: CaseObject, demo_mode = false, hospitals?: string[]) =>
    post<AssistanceResponse>("/api/v1/assistance/match", { case: c, demo_mode, hospitals }),
  appealDraft: (c: CaseObject, denial_text: string, opts: { letter_type?: "internal_appeal" | "documentation_request"; patient_facts?: Record<string, string>; demo_mode?: boolean } = {}) =>
    post<AppealResponse>("/api/v1/appeal/draft", { case: c, denial_text, letter_type: opts.letter_type ?? "internal_appeal", patient_facts: opts.patient_facts ?? {}, demo_mode: opts.demo_mode ?? false }),
};

// ---------- send the appeal letter from the patient's OWN Gmail (no sign-in, no stored credentials) ----------
// Opens Gmail's compose window with the letter filled in. Nothing is sent until the patient presses Send in Gmail.
// If the letter is too long for a link, only To + Subject are filled in and `bodyIncluded` is false: show the letter in a
// copy box and tell the patient to paste it. Keep in step with frontend/compose_link.py.
export function gmailComposeUrl(to: string, subject: string, body: string): { url: string; bodyIncluded: boolean } {
  const MAX_URL = 1800;
  const enc = (s: string) => encodeURIComponent(s ?? "");
  const base = `https://mail.google.com/mail/?view=cm&fs=1&to=${enc((to ?? "").trim()).replace(/%40/g, "@")}&su=${enc(subject)}`;
  const full = `${base}&body=${enc(body)}`;
  return full.length <= MAX_URL ? { url: full, bodyIncluded: true } : { url: base, bodyIncluded: false };
}
