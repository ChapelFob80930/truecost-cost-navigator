from scoring import matches, score_note, summarize


def out(*pairs):
    return [{"code": c, "confidence": f} for c, f in pairs]


def key(**kw):
    base = {"id": 1, "scenario": "t", "expected": [], "expected_groups": [], "acceptable": [],
            "must_not_include": [], "conf_caps": []}
    return {**base, **kw}


def test_matches_family_and_exact():
    assert matches("C50.911", "C50") and matches("c50.911", "C50")
    assert matches("R92.8", "R92.8") and not matches("R92.81", "R92.8")
    assert not matches("C505", "C50")


def test_perfect_note():
    s = score_note(key(expected=["77066"]), out(("77066", 0.95)), [{"code": "77066"}], ["77066"])
    assert (s["precision"], s["recall"]) == (1.0, 1.0)


def test_false_positive_and_miss():
    s = score_note(key(expected=["77066", "76642"]), out(("77066", 0.9), ("77067", 0.9)), [], ["77066"])
    assert s["recall"] == 0.5 and s["precision"] == 0.5 and s["fp"] == ["77067"]


def test_acceptable_not_penalised_and_not_required():
    s = score_note(key(expected=["77066"], acceptable=["N63"]), out(("77066", 0.9), ("N63.10", 0.7)), [], [])
    assert s["precision"] == 1.0 and s["recall"] == 1.0


def test_group_satisfied_by_any_member():
    s = score_note(key(expected_groups=[["N63", "R92.8"]]), out(("R92.8", 0.8)), [], [])
    assert s["recall"] == 1.0 and s["precision"] == 1.0


def test_must_not_and_cap_violation():
    k = key(must_not_include=["C50"], acceptable=["19083"], conf_caps=[{"codes": ["19083"], "max": 0.4}])
    s = score_note(k, out(("C50.911", 0.9), ("19083", 0.8)), [], [])
    assert s["must_not_hits"] == ["C50.911"] and s["conf_cap_violations"] == ["19083"]


def test_empty_expected_and_empty_output_is_perfect():
    s = score_note(key(), [], [], [])
    assert s["precision"] == 1.0 and s["recall"] == 1.0


def test_hallucination_counts_raw_off_list_codes():
    s = score_note(key(), [], [{"code": "99999"}, {"code": "77066"}], ["77066"])
    assert s["hallucinated"] == ["99999"]
    assert summarize([s])["hallucinated_code_rate"] == 0.5
