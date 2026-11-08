"""Streamlit UI for the two assistant flows: help paying for care, and drafting an appeal for a denial.
Called from app.py:  render_agents(api, api_base)   (api is app.py's request helper; api_base is the server address, used for the live stream)."""
from __future__ import annotations

import json
import re

import requests
import streamlit as st

from compose_link import compose_url

SARAH_DENIAL = (
    "Dear Sarah Demo, claim number DEMO-0001: we denied CPT 19083 (ultrasound-guided breast biopsy) on 03/04/2026 "
    "because the service was not medically necessary. Member ID: DEMO12345. Your plan: Univera PPO Plus. "
    "Please send the radiology report if you want us to review this."
)
DISCLAIMER = ("Educational information only: not medical, legal or financial advice. This can't tell you whether you "
              "qualify for anything or whether a denial is right. Talk to a patient navigator, the hospital billing "
              "office, or your care team before you act.")


def _case_from_session(ss) -> dict:
    """Build the shared case object from what the patient already entered. Lives only in this browser session."""
    inp = ss.get("est_inputs") or {}
    ins = inp.get("insurance") or {}
    est = (ss.get("est") or {}).get("results", [])
    case = {
        "confirmed_codes": inp.get("codes", []),
        "estimates": [{"hospital": r["hospital"], "patient_oop": r["patient_oop"], "total_allowed": r["total_allowed"],
                       "complete": r["complete"]} for r in est],
        "uninsured": bool(ss.get("uninsured")),
    }
    if ins and not case["uninsured"]:
        case.update(insurer=ins["insurer"], plan=ins["plan"], deductible_remaining=ins["deductible_remaining"],
                    coinsurance_pct=ins["coinsurance_pct"], oop_max_remaining=ins["oop_max_remaining"])
    return case


def trail_panel(meta: dict) -> None:
    """The 'agent reasoning trail': every tool the assistant used, with inputs, outputs and sources."""
    n = meta.get("tool_calls_used", 0)
    with st.expander(f"How the assistant got this answer ({n} step{'s' if n != 1 else ''})"):
        if meta["mode"] == "cached":
            st.info("This is a pre-recorded demo result. The steps below are from when it was recorded.")
        else:
            st.caption(f"AI model: {meta.get('model')}. The AI never does the math or looks up facts itself; "
                       "it can only call the tools listed here.")
        if not meta["trail"]:
            st.write("No tools were used.")
        for t in meta["trail"]:
            st.markdown(f"**Step {t['step']}: `{t['tool']}`** - {'worked' if t['ok'] else 'failed'} ({t['duration_ms']} ms)")
            if t.get("source"):
                st.caption(f"Source: {t['source']}")
            c1, c2 = st.columns(2)
            with c1:
                st.caption("What it was asked")
                st.json(t["inputs"], expanded=False)
            with c2:
                st.caption("What came back")
                st.json(t["output"] if t["ok"] else {"error": t["error"]}, expanded=False)


ICON = {"met": "✅", "not_met": "❌", "unknown": "❔", "not_applicable": "➖"}
DECISION_LABEL = {"may_apply": "May be worth asking about", "need_more_info": "Need more information",
                  "ruled_out": "Ruled out", "skipped": "Not shown"}


def explain_panel(ex: dict) -> None:
    """Why the assistant did each step and why each program is, or isn't, listed. Reasons are written by code."""
    if not ex or not ex.get("steps"):
        return
    with st.expander("Why this answer? Reasons for every step", expanded=False):
        st.caption("Every reason below is written by our code from the real numbers it used, not by the AI. "
                   "Where the AI helped, it says so and says what checked it.")
        for s in ex["steps"]:
            st.markdown(f"**{s['n']}. {s['action']}**")
            st.markdown(f"- **Why:** {s['why']}")
            st.markdown(f"- **Found:** {s['result']}")
            st.caption(f"Done by: {s['who']}" + (f" · Source: {s['source']}" if s.get("source") else ""))
        if ex.get("program_checks"):
            st.markdown("##### Program by program")
            for c in ex["program_checks"]:
                with st.container(border=True):
                    st.markdown(f"**{c['program_name']}** · {DECISION_LABEL[c['decision']]}")
                    st.markdown(c["reason"])
                    st.table({"Rule": [x["criterion"] for x in c["criteria"]],
                              "Your case": [x["your_value"] for x in c["criteria"]],
                              "Program requires": [x["program_rule"] for x in c["criteria"]],
                              "": [ICON[x["result"]] for x in c["criteria"]]})
                    if c.get("source_url"):
                        st.markdown(f"[Official source]({c['source_url']})")
        if ex.get("not_checked"):
            st.markdown("##### What we did not check")
            for n in ex["not_checked"]:
                st.markdown(f"- {n}")


def _react_step_md(st_, step: dict, observation: str | None = None) -> None:
    """One ReAct round: Thought -> Action -> Observation."""
    who = "the AI" if step["thought_by"] == "ai" else "our code"
    st_.markdown(f"**Step {step['n']}**")
    st_.markdown(f"💭 **Thought** _(written by {who})_: {step['thought']}")
    ran = "the AI chose this action" if step["run_by"] == "ai" else "our code ran this itself"
    st_.markdown(f"🛠️ **Action** _({ran})_: {step['action']}")
    obs = step.get("observation") if observation is None else observation
    st_.markdown(f"👀 **Observation**: {obs}" if obs else "👀 **Observation**: _working..._")


def react_panel(react: list, expanded: bool = True) -> None:
    """The saved trace, shown after the run."""
    if not react:
        return
    with st.expander("How the assistant thought it through (Thought, Action, Observation)", expanded=expanded):
        st.caption("Thoughts marked 'the AI' are the AI's own words, shown only after our checks. Observations are always written by "
                   "our code from the real result of each action.")
        for step in react:
            with st.container(border=True):
                _react_step_md(st, step)


def _stream_assistance(api_base: str, body: dict):
    """Run the assistant and show each Thought / Action / Observation live. Returns the final response, or None."""
    final = None
    status = st.status("The assistant is working...", expanded=True)
    slots: dict = {}
    try:
        with requests.post(f"{api_base}/api/v1/assistance/match/stream", json=body, stream=True, timeout=(10, 180)) as r:
            if r.status_code >= 400:
                status.update(label="Something went wrong", state="error")
                st.error(r.text[:300])
                return None
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                with status:
                    if ev["type"] == "step_start":
                        slots[ev["step"]["n"]] = (st.empty(), ev["step"])
                        box, step = slots[ev["step"]["n"]]
                        with box.container(border=True):
                            _react_step_md(st, step)
                    elif ev["type"] == "step_done" and ev["n"] in slots:
                        box, step = slots[ev["n"]]
                        with box.container(border=True):
                            _react_step_md(st, step, ev["observation"])
                    elif ev["type"] == "final":
                        final = ev["response"]
                    elif ev["type"] == "error":
                        status.update(label="Something went wrong", state="error")
                        st.error(ev["detail"])
                        return None
    except requests.RequestException as e:
        status.update(label="Can't reach the server", state="error")
        st.error(f"Can't reach the TrueCost server. ({e.__class__.__name__})")
        return None
    status.update(label="Done. Here is what the assistant found.", state="complete", expanded=False)
    return final


def _meta_footer(meta: dict) -> None:
    if meta.get("error"):
        st.warning(meta["error"])
    badge = "Pre-recorded demo" if meta["mode"] == "cached" else "Live AI answer"
    st.markdown(f'<span class="pill {"pill-grey" if meta["mode"] == "cached" else "pill-green"}">{badge}</span>', unsafe_allow_html=True)
    trail_panel(meta)
    for d in meta["disclaimers"]:
        st.caption(d)
    st.markdown(f"**{meta['handoff']}**")


# ----------------------------------------------------------------------------- flow 1
def _assistance(api, case: dict, demo: bool, api_base: str) -> None:
    st.header("5. Help paying for care")
    st.markdown("Some programs help with medical bills. Tell us about your household and we will look up programs "
                "**you may want to ask about**. We can't say whether you qualify. Only the program can.")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.checkbox("I don't have health insurance", key="uninsured")
    with c2:
        hh = st.number_input("People in your household", min_value=1, max_value=20, value=3, step=1)
    with c3:
        inc = st.number_input("Household income per year ($)", min_value=0, value=52000, step=1000,
                              help="Optional. Used only in this session to compare with the Federal Poverty Level.")
    use_income = st.checkbox("Use my household size and income to match programs", value=True)
    if st.button("Look for help programs", type="primary"):
        body = {"case": {**_case_from_session(st.session_state),
                         **({"household_size": int(hh), "annual_income": float(inc)} if use_income else {})},
                "demo_mode": demo}
        st.session_state["assist"] = _stream_assistance(api_base, body)

    r = st.session_state.get("assist")
    if not r:
        return
    if r.get("fpl"):
        f = r["fpl"]
        st.markdown(f"Your income is about **{f['fpl_percent']:g}%** of the Federal Poverty Level "
                    f"({f['guideline_year']} guidelines, household of {f['household_size']}).")
        if f["data_status"] != "VERIFIED":
            st.warning("The poverty-level figures used here are still being double-checked by our team.")
    note = r.get("income_note")
    if note:
        with st.container(border=True):
            st.markdown(f"#### {note['headline']}")
            st.markdown(note["detail"])
            st.markdown("**Other things you can try to lower what you pay:**")
            for tip in note["suggestions"]:
                st.markdown(f"- {tip}")
    else:
        st.markdown(f"### {r['summary_plain']}")
    for m in r["matches"]:
        with st.container(border=True):
            st.markdown(f"#### {m['program_name']}")
            st.markdown(f'<span class="muted">{m["sponsor"]} - {m["type"]}</span>', unsafe_allow_html=True)
            st.markdown(m["why"])
            if m.get("eligibility_summary"):
                st.markdown(f"**What the program says:** {m['eligibility_summary']}")
            if m.get("contact"):
                st.markdown(f"**Contact:** {m['contact']}")
            st.markdown(f"**Next step:** {m['next_step']}")
            with st.expander("Questions to ask the billing office"):
                for q in m["questions_for_billing_office"]:
                    st.markdown(f"- {q}")
            st.markdown(f"[Official source]({m['source_url']})")
    for n in r["not_found"]:
        st.info(n)
    react_panel((r.get("explanation") or {}).get("react", []), expanded=False)
    explain_panel(r.get("explanation"))
    _meta_footer(r["meta"])


# ----------------------------------------------------------------------------- send by email (after approval)
_BLANK = re.compile(r"\[[A-Z][A-Z0-9 _/'-]{1,40}\]")


def _gmail_header() -> dict:
    sid = st.session_state.get("gmail_sid")
    return {"X-Gmail-Session": sid} if sid else {}


def _gmail_connect_ui(api) -> str | None:
    """Shows the connect / disconnect controls. Returns the connected email, or None."""
    status = api("GET", "/api/v1/gmail/status", headers=_gmail_header())
    if not status["available"]:
        st.info("Emailing isn't set up on this server. Download the draft above and send it yourself.")
        return None
    if status["connected"]:
        st.success(f"Gmail connected: {status['email']}. Drafts are saved in, and sent from, your own account.")
        if st.button("Disconnect my Gmail"):
            api("POST", "/api/v1/gmail/disconnect", headers=_gmail_header())
            st.session_state.pop("gmail_sid", None)
            st.session_state.pop("gmail_url", None)
            st.rerun()
        return status["email"]
    st.markdown("To save this letter as a draft in Gmail, connect **your own Gmail**. We ask Google only for permission "
                "to create drafts and send an email you approve here. We can't read your inbox, and you can disconnect any time.")
    if st.button("Connect my Gmail"):
        res = api("POST", "/api/v1/gmail/connect")
        st.session_state["gmail_sid"], st.session_state["gmail_url"] = res["session_id"], res["auth_url"]
    if st.session_state.get("gmail_url"):
        st.link_button("1. Sign in with Google (opens a new tab)", st.session_state["gmail_url"])
        if st.button("2. I finished connecting: check"):
            st.rerun()
    return None


def _compose_panel(text: str, parsed: dict) -> None:
    """Open a prefilled Gmail compose window. We never see the patient's Gmail; they review and press Send themselves."""
    st.subheader("Send it from your own Gmail (optional)")
    st.caption("We open Gmail with this letter filled in. Nothing is sent until you press Send in Gmail. "
               "Check where your plan accepts appeals first; many use a portal, fax or mail.")
    blanks = sorted(set(_BLANK.findall(text)))
    if blanks:
        st.warning("Fill in these blanks in the draft above first: " + ", ".join(blanks))
    to = st.text_input("Send to (type the address from your plan's letter or website)", key="send_to")
    subject = st.text_input("Subject", key="send_subject", value="Appeal" + (
        f" - claim {parsed['claim_or_reference_number']}" if parsed.get("claim_or_reference_number") else ""))
    url, included = compose_url(to, subject, text)
    st.link_button("Open in Gmail", url, disabled=bool(blanks))
    if not included:
        st.info("This letter is too long to fill in automatically. Gmail will open with the address and subject. "
                "Copy the letter below (the copy icon) and paste it in.")
    with st.expander("Copy the letter"):
        st.code(text, language=None)


def _send_panel(api, text: str, parsed: dict, demo: bool) -> None:
    """Draft here -> connect the patient's own Gmail -> save a Gmail draft -> send only after they approve."""
    st.subheader("Put it in your Gmail (optional)")
    st.caption("Your draft stays here until you connect Gmail. Then we save it as a draft in your own Gmail. "
               "Nothing is sent until you approve. Check where your plan accepts appeals first; many use a portal, fax or mail.")
    if demo or not api("GET", "/api/v1/gmail/status")["available"]:
        _compose_panel(text, parsed)          # default: no sign-in, you send it yourself from Gmail
        return
    me = _gmail_connect_ui(api)
    if me is None:
        return
    to = st.text_input("Send to (type the address from your plan's letter or website)", key="send_to")
    default_subject = "Appeal" + (f" - claim {parsed['claim_or_reference_number']}" if parsed.get("claim_or_reference_number") else "")
    subject = st.text_input("Subject", value=default_subject, key="send_subject")

    if st.button("Create draft in my Gmail"):
        res = api("POST", "/api/v1/gmail/draft", headers=_gmail_header(), json={"to": to, "subject": subject, "body": text})
        st.session_state["gmail_draft_id"] = res["draft_id"]
    draft_id = st.session_state.get("gmail_draft_id")
    if not draft_id:
        return
    st.success(f"Saved as a draft in {me}. [Open your Gmail drafts](https://mail.google.com/mail/u/0/#drafts)")
    st.caption("If you change the letter above, we update the Gmail draft with the final text when you send.")

    blanks = sorted(set(_BLANK.findall(text)))
    if blanks:
        st.warning("Fill in these blanks in the draft above to enable sending: " + ", ".join(blanks))
    approved = st.checkbox(f"I reviewed this letter and approve sending it from {me}.", key="send_ok")
    if st.button("Approve and send", disabled=bool(blanks) or not approved or "@" not in to):
        res = api("POST", "/api/v1/appeal/send", headers=_gmail_header(),
                  json={"to": to, "subject": subject, "body": text, "approved": True, "draft_id": draft_id})
        st.session_state.pop("gmail_draft_id", None)
        st.success(f"Sent from {res['sent_from']} to {res['to']}.")


# ----------------------------------------------------------------------------- flow 2
def _appeal(api, case: dict, demo: bool) -> None:
    st.header("6. Got a denial letter?")
    st.markdown("Paste the letter (or describe what happened). We will explain it in plain English and draft an appeal "
                "letter for you to review with your care team. **Private details like your name and member ID are hidden "
                "from the AI.**")
    st.button("Try a made-up denial letter (Sarah)", on_click=lambda: st.session_state.update(denial=SARAH_DENIAL))
    st.text_area("Denial letter text", key="denial", height=170)
    kind = st.radio("What do you want to write?", ["Appeal the denial", "Ask the plan for more information"], horizontal=True)
    with st.expander("Optional: details to fill into the letter (never sent to the AI)"):
        name = st.text_input("Your name", key="pf_name")
        member = st.text_input("Member ID", key="pf_member")
        today = st.text_input("Today's date", key="pf_date")
    if st.button("Draft my letter", type="primary", disabled=len(st.session_state.get("denial", "").strip()) < 3):
        facts = {"patient_name": name, "member_id": member, "date": today}
        body = {"case": _case_from_session(st.session_state), "denial_text": st.session_state["denial"],
                "letter_type": "internal_appeal" if kind.startswith("Appeal") else "documentation_request",
                "patient_facts": {k: v for k, v in facts.items() if v}, "demo_mode": demo}
        with st.spinner("Reading the letter and drafting..."):
            st.session_state["appeal"] = api("POST", "/api/v1/appeal/draft", json=body)

    r = st.session_state.get("appeal")
    if not r:
        return
    st.subheader("What the letter says, in plain English")
    st.markdown(r["denial_summary_plain"])
    for fl in r["case_check"]["flags"]:
        st.warning(fl["message"])
    p = r["parsed"]
    with st.expander("What we found in the letter"):
        st.table({"Item": ["Reason given", "Service or code", "Date", "Claim number", "Appeal deadline stated", "Plan"],
                  "From your letter": [p["denial_reason"] or "not stated", p["denied_service_or_code"] or "not stated",
                                       p["date"] or "not stated", p["claim_or_reference_number"] or "not stated",
                                       p["appeal_deadline_if_stated"] or "not stated", p["plan_name"] or "not stated"]})
    st.subheader("Ask your care team for")
    for item in r["documentation_checklist"]:
        st.checkbox(item, key=f"doc_{item}")
    st.subheader("Draft letter (edit it, then review with your care team)")
    if r["letter"]["placeholders"]:
        st.info("Fill in these blanks before sending: " + ", ".join(r["letter"]["placeholders"]))
    text = st.text_area("Your draft", value=r["letter"]["body"], height=380, key="letter_text")
    st.download_button("Download the draft (.txt)", data=text, file_name="appeal_draft.txt")
    _send_panel(api, text, p, demo)
    st.subheader("Next steps")
    for s in r["next_steps"]:
        st.markdown(f"- {s}")
    explain_panel(r.get("explanation"))
    _meta_footer(r["meta"])


# ----------------------------------------------------------------------------- entry point
def render_agents(api, api_base: str) -> None:
    st.divider()
    st.markdown("## Beyond the price")
    st.markdown(f'<div class="caveat">{DISCLAIMER}</div>', unsafe_allow_html=True)
    demo = st.toggle("Demo mode: use pre-recorded answers (works without internet or the AI)", key="demo_mode")
    case = _case_from_session(st.session_state)
    _assistance(api, case, demo, api_base)
    _appeal(api, case, demo)
