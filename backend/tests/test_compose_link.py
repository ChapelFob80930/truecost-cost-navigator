import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "frontend"))
from compose_link import MAX_URL, compose_url  # noqa: E402


def test_short_letter_is_fully_prefilled_and_round_trips():
    body = "Dear plan,\nMember ID: 123 & claim #4?\nThanks"
    url, included = compose_url(" appeals@plan.example ", "Appeal - claim DEMO-0001", body)
    q = parse_qs(urlparse(url).query)
    assert included and urlparse(url).netloc == "mail.google.com"
    assert q["to"] == ["appeals@plan.example"] and q["su"] == ["Appeal - claim DEMO-0001"] and q["body"] == [body]


def test_long_letter_drops_the_body_and_says_so():
    url, included = compose_url("a@b.co", "Appeal", "word " * 2000)
    assert not included and len(url) <= MAX_URL and "body=" not in url


def test_special_characters_in_subject_cannot_inject_extra_fields():
    url, _ = compose_url("a@b.co", "x&bcc=evil@x.com", "hi")
    q = parse_qs(urlparse(url).query)
    assert "bcc" not in q and q["su"] == ["x&bcc=evil@x.com"]
