"""Streamlit UI for the two assistant flows: help paying for care, and drafting an appeal for a denial.
Called from app.py:  render_agents(api, money, codes_ref)   (api/money are app.py's helpers)."""
from __future__ import annotations

import streamlit as st

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
def _assistance(api, case: dict, demo: bool) -> None:
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
        with st.spinner("Looking..."):
            st.session_state["assist"] = api("POST", "/api/v1/assistance/match", json=body)

    r = st.session_state.get("assist")
    if not r:
        return
    if r.get("fpl"):
        f = r["fpl"]
        st.markdown(f"Your income is about **{f['fpl_percent']:g}%** of the Federal Poverty Level "
                    f"({f['guideline_year']} guidelines, household of {f['household_size']}).")
        if f["data_status"] != "VERIFIED":
            st.warning("The poverty-level figures used here are still being double-checked by our team.")
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
    _meta_footer(r["meta"])


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
    st.subheader("Next steps")
    for s in r["next_steps"]:
        st.markdown(f"- {s}")
    _meta_footer(r["meta"])


# ----------------------------------------------------------------------------- entry point
def render_agents(api) -> None:
    st.divider()
    st.markdown("## Beyond the price")
    st.markdown(f'<div class="caveat">{DISCLAIMER}</div>', unsafe_allow_html=True)
    demo = st.toggle("Demo mode: use pre-recorded answers (works without internet or the AI)", key="demo_mode")
    case = _case_from_session(st.session_state)
    _assistance(api, case, demo)
    _appeal(api, case, demo)
