"""TrueCost - Streamlit front end. Talks to the FastAPI backend (TRUECOST_API, default http://127.0.0.1:8000)."""
from __future__ import annotations

import os

import altair as alt
import pandas as pd
import requests
import streamlit as st

from agents_ui import render_agents

API = os.getenv("TRUECOST_API", "http://127.0.0.1:8000")

SARAH_NOTE = (
    "Patient: Sarah (made-up demo patient), 52. Found a lump in her LEFT breast two weeks ago. "
    "Screening imaging shows a suspicious mass, BI-RADS 4. Cancer has not been confirmed. "
    "Plan: left diagnostic mammogram and a limited ultrasound of the left breast, then an "
    "ultrasound-guided core biopsy of the mass, with the tissue sent for surgical pathology."
)

# Illustrative "what if it is cancer" journey, used only for the cost curve.
TREATMENT_STEPS = [
    ("19301", 3, 1, "Lumpectomy"),
    ("38525", 3, 1, "Lymph node removal"),
    ("96413", 4, 6, "Chemotherapy infusions"),
    ("77385", 10, 5, "Radiation treatments"),
]

st.set_page_config(page_title="TrueCost - know before you owe", page_icon=":hospital:", layout="wide")

st.markdown(
    """
<style>
html, body, [class*="css"], .stMarkdown, label, .stTextArea textarea, .stNumberInput input { font-size: 20px !important; }
[data-testid="stWidgetLabel"] p, [data-testid="stCheckbox"] p, .stSelectbox div, .stMultiSelect div { font-size: 20px !important; color:#111; }
[data-testid="stCaptionContainer"] p { font-size: 17px !important; color:#333; }
h1 { font-size: 2.6rem !important; color:#0B3C6B; } h2 { font-size: 1.9rem !important; color:#0B3C6B; }
.stButton button { font-size: 20px !important; padding: .7rem 1.4rem; border-radius: 10px; font-weight: 600; }
.caveat { background:#FFF4D6; border:2px solid #B07B00; color:#3B2A00; padding:.8rem 1rem; border-radius:10px; font-size:18px; }
.big-money { font-size: 2.6rem; font-weight: 800; color:#0B3C6B; line-height:1.1; }
.best { border: 3px solid #1B7F3B !important; }
.muted { color:#4A4A4A; font-size:17px; }
.pill { display:inline-block; background:#E6F1FB; color:#0B3C6B; padding:.1rem .6rem; border-radius:999px; font-size:16px; font-weight:600; }
.pill-green { background:#DDF3E4; color:#0E5A27; }
.pill-grey { background:#E8E8E8; color:#333; }
</style>
""",
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------- API helpers
def api(method: str, path: str, **kw):
    try:
        r = requests.request(method, f"{API}{path}", timeout=120, **kw)
    except requests.RequestException as e:
        st.error(f"Can't reach the TrueCost server at {API}. Is it running? ({e.__class__.__name__})")
        st.stop()
    if r.status_code >= 400:
        detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
        if isinstance(detail, list):
            detail = "; ".join(str(d.get("msg", d)) for d in detail)
        st.error(f"Something went wrong: {detail}")
        st.stop()
    return r.json()


@st.cache_data(ttl=300)
def load_codes():
    return {c["code"]: c for c in api("GET", "/api/v1/codes")}


@st.cache_data(ttl=300)
def load_plans(insurer: str):
    return api("GET", "/api/v1/plans", params={"insurer": insurer})


def money(x, cents=False):
    return f"${x:,.2f}" if cents else f"${x:,.0f}"


# ----------------------------------------------------------------------------- header
st.title("TrueCost")
st.markdown("### Know before you owe: compare what Buffalo-area hospitals would charge *you* for breast care tests")
st.markdown(
    "Upload a doctor's note. We find the billing codes in it, you confirm them, tell us about your insurance, "
    "and we rank local hospitals by what you would likely pay out of pocket."
)
st.markdown(
    '<div class="caveat"><b>Please read:</b> these are <b>estimates only</b> &bull; hospital (facility) fees only, '
    "doctor and radiologist bills are separate &bull; based on public hospital price files &bull; "
    "this demo uses <b>made-up patients only</b> &mdash; no real patient data.</div>",
    unsafe_allow_html=True,
)

codes_ref = load_codes()
ss = st.session_state
ss.setdefault("note", "")
ss.setdefault("found", None)
ss.setdefault("run", 0)


# ----------------------------------------------------------------------------- step 1
st.header("1. Your doctor's note")


def _load_sarah():
    ss["note"] = SARAH_NOTE
    ss["found"] = None


st.button("Try the demo: Sarah's note", on_click=_load_sarah)
up = st.file_uploader("...or upload a text file (.txt)", type=["txt"])
if up is not None and ss.get("_up_name") != up.name:
    ss["note"] = up.read().decode("utf-8", errors="ignore")
    ss["_up_name"], ss["found"] = up.name, None
st.text_area("Paste or edit the note here", key="note", height=190)

if st.button("Find the billing codes in my note", type="primary", disabled=len(ss["note"].strip()) < 3):
    with st.spinner("Reading the note..."):
        ss["found"] = api("POST", "/extract", json={"note": ss["note"]})
        ss["run"] += 1
        ss.pop("est", None)
        ss.pop("sims", None)

selected: list[str] = []
if ss["found"] is not None:
    st.subheader("Is this right? Check each code")
    found = ss["found"]["codes"]
    if not found:
        st.info("We didn't find any supported breast-care services in this note. You can add codes by hand below.")
    for c in found:
        key = f"chk_{ss['run']}_{c['code']}"
        low = c["confidence"] < 0.6
        left, right = st.columns([3, 2])
        with left:
            checked = st.checkbox(f"**{c['code']}** - {c['description']}", value=not low and c["type"] != "ICD10", key=key)
        with right:
            conf_txt = f"{c['confidence']:.0%} sure" + (" - please double-check" if low else "")
            st.markdown(f'<span class="pill {"pill-grey" if low else "pill-green"}">{conf_txt}</span> '
                        f'<span class="muted">from your note: "{c["evidence"]}"</span>', unsafe_allow_html=True)
        if checked:
            selected.append(c["code"])
    extra_options = [k for k, v in codes_ref.items() if v["code_type"] != "ICD10" and k not in {c["code"] for c in found}]
    extra = st.multiselect("Add another service (optional)", extra_options,
                           format_func=lambda k: f"{k} - {codes_ref[k]['description']}")
    selected += extra
    st.caption(f"Read by: `{ss['found']['extractor']}`. Diagnosis codes (like N63 or C50) describe the condition; "
               "only procedure codes get a price.")

# order: diagnostic services first, then treatment (the order uses up the deductible)
phase_rank = {"diagnostic": 0, "treatment": 1}
billable = [c for c in selected if codes_ref.get(c, {}).get("code_type") != "ICD10"]
billable.sort(key=lambda c: phase_rank.get(codes_ref[c]["phase"], 2))


# ----------------------------------------------------------------------------- step 2
if ss["found"] is not None:
    st.header("2. Your insurance")
    st.markdown('<span class="muted">Find these on your insurance card or member portal. Not sure? The defaults are typical.</span>',
                unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        insurer = st.selectbox("Insurance company", ["Independent Health", "Highmark BCBS", "Univera"])
        plans = load_plans(insurer)
        plan = st.selectbox("Plan type", plans, index=plans.index("Any") if "Any" in plans else 0,
                            help="Pick 'Any' if you are not sure; we then use the middle price across all plan types.")
        zip_code = st.text_input("Your ZIP code (for drive time)", value="14226", max_chars=5)
    with c2:
        ded = st.number_input("How much of your deductible is left? ($)", min_value=0, value=1200, step=100)
        coins = st.number_input("Your share after the deductible (coinsurance, %)", min_value=0, max_value=100, value=20, step=5)
        oopm = st.number_input("How much is left until your yearly out-of-pocket maximum? ($)", min_value=0, value=4000, step=500)
    insurance = {"insurer": insurer, "plan": plan, "deductible_remaining": ded,
                 "coinsurance_pct": coins, "oop_max_remaining": oopm}

    # ------------------------------------------------------------------------- step 3
    st.header("3. Compare hospitals")
    if not billable:
        st.info("Check at least one service above to compare prices.")
    elif st.button("Compare hospitals", type="primary"):
        with st.spinner("Comparing prices..."):
            ss["est"] = api("POST", "/estimate", json={"codes": billable, "insurance": insurance, "patient_zip": zip_code})
            ss["est_inputs"] = {"codes": billable, "insurance": insurance, "zip": zip_code}
            ss.pop("sims", None)

    est = ss.get("est")
    if est:
        results = est["results"]
        ranked = [r for r in results if r["complete"]]
        others = [r for r in results if not r["complete"]]
        if ranked:
            best, worst = ranked[0], ranked[-1]
            saving = worst["patient_oop"] - best["patient_oop"]
            st.markdown(f"## Lowest estimate: **{best['hospital']}** at about **{money(best['patient_oop'])}**")
            if saving > 0:
                st.markdown(f"That is about **{money(saving)} less** than the most expensive option ({worst['hospital']}).")
            else:
                st.markdown("All of these hospitals come out about the same for you.")
        else:
            st.warning("No hospital has published prices for every service you selected, so we can't rank them.")

        for i, r in enumerate(ranked, start=1):
            with st.container(border=True):
                a, b, c = st.columns([4, 2, 2])
                with a:
                    st.markdown(f"### #{i} {r['hospital']}" + ("  <span class='pill pill-green'>lowest cost</span>" if i == 1 else ""),
                                unsafe_allow_html=True)
                    st.markdown(f'<span class="muted">{r["address"] or ""}</span>', unsafe_allow_html=True)
                    if r["drive_minutes"] is not None:
                        st.markdown(f"About **{r['drive_minutes']} min** drive")
                with b:
                    st.markdown("You would pay about")
                    st.markdown(f'<div class="big-money">{money(r["patient_oop"])}</div>', unsafe_allow_html=True)
                with c:
                    st.markdown("Insurance would allow")
                    st.markdown(f"**{money(r['total_allowed'])}**")
                    if r["savings_vs_highest"]:
                        st.markdown(f'<span class="pill pill-green">saves {money(r["savings_vs_highest"])}</span>', unsafe_allow_html=True)
                with st.expander("See the breakdown"):
                    df = pd.DataFrame([{"Service": f"{l['code']} {l['description'] or ''}", "Insurance rate": money(l["rate"], True),
                                        "You pay": money(l["patient_pays"], True), "How we got this": l["rule"]} for l in r["lines"]])
                    st.dataframe(df, hide_index=True, use_container_width=True)
                    for u in r["unavailable"]:
                        st.markdown(f"- **{u['code']}**: {u['reason']}")

        if others:
            st.subheader("Can't be compared fairly")
            for r in others:
                with st.container(border=True):
                    st.markdown(f"**{r['hospital']}** - missing a published price for: " +
                                ", ".join(f"{u['code']}" for u in r["unavailable"] if "contact hospital" not in u["reason"]))
                    with st.expander("Why?"):
                        for u in r["unavailable"]:
                            st.markdown(f"- **{u['code']}**: {u['reason']}")

        pts = pd.DataFrame([{"lat": r["lat"], "lon": r["lng"], "color": "#1B7F3B" if r["complete"] else "#888888"}
                            for r in results if r["lat"] is not None])
        if not pts.empty:
            st.subheader("Where they are")
            st.map(pts, latitude="lat", longitude="lon", color="color", size=250, zoom=10)
            st.caption("Green = ranked above. Grey = couldn't be compared.")
        with st.expander("Important notes"):
            for cv in est["caveats"]:
                st.markdown(f"- {cv}")

        # --------------------------------------------------------------------- step 4
        st.header("4. How costs add up over the journey")
        st.markdown("Your yearly out-of-pocket maximum caps what you pay. This shows month by month when you would get there.")
        inp = ss["est_inputs"]
        with_tx = st.checkbox("Include a possible treatment journey (lumpectomy, lymph nodes, chemotherapy, radiation) "
                              "- illustrative only, in case a diagnosis is confirmed", value=True)
        options = [r["hospital"] for r in ranked] or [r["hospital"] for r in results]
        chosen = st.multiselect("Hospitals to compare", options, default=options[:1] + options[-1:] if len(options) > 1 else options)

        if st.button("Show my cost over time", type="primary") and chosen:
            diag = [c for c in inp["codes"] if codes_ref[c]["phase"] != "treatment"]
            steps = []
            for c in diag:
                steps.append({"code": c, "month": 2 if c == "88305" else 1, "step": codes_ref[c]["description"], "repeat": 1})
            if with_tx:
                for code, month, rep, label in TREATMENT_STEPS:
                    steps.append({"code": code, "month": month, "step": label, "repeat": rep})
            all_codes = list(dict.fromkeys([s["code"] for s in steps]))
            sims = {}
            with st.spinner("Building your timeline..."):
                for h in chosen:
                    sims[h] = api("POST", "/simulate", json={"codes": all_codes, "insurance": inp["insurance"],
                                                              "patient_zip": inp["zip"], "hospital": h, "pathway": steps})
            ss["sims"] = sims
            ss["oopm"] = inp["insurance"]["oop_max_remaining"]

        sims = ss.get("sims")
        if sims:
            rows = []
            for h, s in sims.items():
                rows.append({"hospital": h, "month": 0, "cumulative": 0.0})
                last = 0.0
                for e in s["timeline"]:
                    rows.append({"hospital": h, "month": e["month"], "cumulative": e["cumulative_oop"]})
                    last = e["cumulative_oop"]
            df = pd.DataFrame(rows).groupby(["hospital", "month"], as_index=False)["cumulative"].max()
            line = alt.Chart(df).mark_line(point=True, strokeWidth=4).encode(
                x=alt.X("month:Q", title="Month of treatment", axis=alt.Axis(tickMinStep=1, labelFontSize=16, titleFontSize=18)),
                y=alt.Y("cumulative:Q", title="Total you have paid ($)", axis=alt.Axis(labelFontSize=16, titleFontSize=18)),
                color=alt.Color("hospital:N", legend=alt.Legend(labelFontSize=16, titleFontSize=16, orient="bottom")),
                tooltip=["hospital", "month", alt.Tooltip("cumulative:Q", format="$,.0f")])
            cap = alt.Chart(pd.DataFrame({"y": [ss["oopm"]]})).mark_rule(strokeDash=[8, 6], color="#B00020", strokeWidth=3).encode(y="y:Q")
            st.altair_chart((line + cap).properties(height=420), use_container_width=True)
            bs = chr(92)  # backslash: escapes "$" so Streamlit doesn't treat text between two $ as math
            esc = money(ss["oopm"]).replace("$", bs + "$")
            st.caption(f"Red dashed line = your out-of-pocket maximum ({esc} left). After you reach it, covered care costs you {bs}$0 for the rest of the year.")
            for h, s in sims.items():
                total = s["timeline"][-1]["cumulative_oop"] if s["timeline"] else 0
                reached = s["oop_max_reached_month"]
                msg = f"you would reach your maximum in **month {reached}**" if reached else "you would **not** reach your maximum"
                st.markdown(f"- **{h}**: total about **{money(total)}**; {msg}.")
                if s["unavailable"]:
                    st.markdown("  - Not priced: " + "; ".join(f"{u['code']} ({u['reason']})" for u in s["unavailable"]))


# ----------------------------------------------------------------------------- assistant flows
render_agents(api)
