from app.domain.plan_types import available_types, classify, parse_selection


def test_classify_keywords_multilabel():
    assert classify("Article 44 HMO") == {"HMO"}
    assert classify("Healthnow HMO/POS") == {"HMO", "POS"}
    assert classify("Article 44 / Article 43 HMO/PPO") == {"HMO", "PPO"}
    assert classify("Choice Plus") == {"Other"}
    assert classify("Community Blue POS") == {"POS"}
    assert classify(None) == {"Other"}


def test_parse_selection():
    assert parse_selection("hmo") == {"HMO"}
    assert parse_selection("HMO/PPO") == {"HMO", "PPO"}
    assert parse_selection("Any") == {"HMO", "PPO", "POS", "Other"}
    assert parse_selection("Gold") is None and parse_selection("") is None


def test_available_types():
    assert available_types(["Choice Plus", "Article 44 HMO"]) == ["HMO", "Other", "Any"]
    assert available_types([]) == []
