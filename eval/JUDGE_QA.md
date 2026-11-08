# The 5 hardest judge questions (and honest answers)

Rule for every answer: say what is built, say the limit, never claim more than the numbers show.

---

## 1. "How do you stop the AI from making up a price or a code?"

**Answer.** The AI never produces a number you see. Prices come from the hospitals' own price files in a database. The deductible, coinsurance and out-of-pocket math is plain, unit-tested code. The AI only does two jobs: it suggests billing codes from a note, and it writes a few plain-English sentences. Both are fenced in by code:
- A suggested code must be on our allowed list, and its quote must appear word for word in the note (15 words or fewer), or it is dropped.
- Any AI sentence that adds a number not found in the tool results, says "you qualify", cites a law, or gives clinical advice is thrown away and replaced with standard wording.

**Proof to show.** Code-extraction eval: 15 synthetic notes, naive prompt 13/15 perfect, our rule prompt 15/15, keyword baseline 6/15, hallucinated-code rate 0.00. Agent eval: 10/10 cases, 94/94 deterministic checks.

**If pressed.** "The naive prompt fell for a prompt injection hidden in the middle of a note (it output a mastectomy code). The guards exist because of that failure."

---

## 2. "Your eval notes were written by you. Isn't 15/15 just overfitting?"

**Answer.** Fair challenge, and partly yes. Be direct about it:
- The notes and the prompt rules were written by the same person (an AI assistant) in the same session, and the answer keys are drafts that a clinician has not verified yet.
- It is 15 notes on two small Gemini "lite" models. We could not get a larger model because of quota limits.
- What the eval does show is the *gap*: the same model scores 13/15 with a naive prompt and 15/15 with ours, and the failures are the dangerous kind (injection, ignoring a declined service).

**What we would do next.** Clinician-verified keys, a held-out set written by someone else, a non-Google second model, and more adversarial notes.

**Do not say** "15/15 proves it is accurate." Say "it is a regression test that shows our guardrails work on the failure cases we thought of."

---

## 3. "You send medical text to an AI. What about privacy and HIPAA?"

**Answer.**
- Everything in the demo is made-up patients. No real patient data is used or stored.
- The case lives only in the browser session. Nothing is saved to a database.
- Before any pasted denial letter reaches the AI, a scrubber replaces labelled names, member IDs, MRNs, dates of birth, claim numbers, phones, emails, SSNs and street addresses with tokens, and puts them back only locally. An eval case checks that none of those raw values appear in anything sent to the model.

**The limit, stated plainly.** The scrubber is pattern-based. It misses a name written in plain prose ("Jane called us"). It is a demo safeguard, not a certified de-identification system. A real product needs a HIPAA-compliant setup (a business associate agreement with the model provider, or a real de-identification service) and a security review.

---

## 4. "Are these prices right? Why is one hospital so much cheaper?"

**Answer.** The prices are the hospitals' published negotiated rates, taken from their price-transparency files (Roswell Park, Kaleida, the Catholic Health hospitals; ECMC publishes only gross charges, so it is left out of the ranking). We show facility fees only, and doctor and radiologist bills are billed separately. Because the files list dozens of plan names, we group them into plan types (HMO, PPO, POS, Other, Any) and use the median within the type, showing the range.

**Be honest about what is not checked.**
- We have **not** yet spot-checked our numbers against an independent source such as medrates.fyi. Roswell Park comes out noticeably cheaper than the Catholic hospitals for some insurers, and we have not confirmed why.
- Some hospitals lack a price for some codes (the Catholic hospitals miss 19085, 19301, 19303, 38525, 77048, 77385 and, for the demo, 19083). When a hospital cannot be fully priced we say "can't be compared" instead of guessing or ranking it.
- Which Radiology-tagged rate counts as "the" rate for Independent Health 77066 is still an open decision ($216.56 vs $307.90).

**Line to use.** "These are estimates to help you ask the right questions, not a quote. Always confirm with the hospital."

---

## 5. "Isn't the assistance agent just a lookup table with a chatbot on top? And what if the table is wrong?"

**Answer.** Partly, and that is the point. It is a bounded agent: it has 3 allowed tools, a hard cap of 5 tool calls, and every call is shown in a reasoning-trail panel with inputs, outputs and sources. Code builds the result (the poverty-level percent, which programs match, the official link). The AI only writes the summary, and it can never say "you qualify". It says "you may want to ask about this" and tells the patient to confirm with the program.

**What protects against a wrong table.** Every row has a `verified` flag. Unverified rows are ignored by the matcher and rejected by a validator. We did not type any eligibility thresholds from memory.

**The honest limit.** Right now the real table has **no verified rows**, so on real data the agent answers "I couldn't find this", which is the correct honest behaviour. The tests use clearly labelled FAKE rows. The poverty-level figures were fetched from the official HHS page but still need a human to confirm them. The hard part of this feature is curating accurate program data, not the AI.

---

## Bonus: the one-line answers

- **"Why not just use ChatGPT?"** It would guess prices and eligibility. Ours reads the real price files and shows its sources.
- **"What did you build in 24 hours?"** Real WNY price data, a tested cost calculator, a cost-over-time simulator, a guarded Gemini code extractor with an eval, and two bounded assistants with a visible reasoning trail.
- **"What is not done?"** A clinician-verified eval, an independent price spot-check, verified assistance data, and a HIPAA-grade privacy setup.
