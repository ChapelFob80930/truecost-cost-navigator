"""Temporary AI errors (503 "high demand", 429) are retried, then a backup model is tried. Real errors are not retried."""
import pytest

from app.agents.llm import GeminiChat
from app.retry import is_transient, retry_transient


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr("app.retry.time.sleep", lambda s: None)     # restored after each test


class Boom(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


def test_which_errors_count_as_temporary():
    assert is_transient(Boom("x", 503)) and is_transient(Boom("x", 429))
    assert is_transient(Boom("503 UNAVAILABLE. This model is currently experiencing high demand"))
    assert is_transient(Boom("read timed out"))
    assert not is_transient(Boom("API key not valid", 400)) and not is_transient(Boom("bad request", 400))
    assert not is_transient(ValueError("something else"))


def test_retries_a_temporary_error_then_succeeds():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise Boom("503 UNAVAILABLE", 503)
        return "ok"
    assert retry_transient(flaky, delays=(0, 0)) == "ok" and len(calls) == 3


def test_gives_up_after_the_last_delay_and_raises_the_last_error():
    calls = []

    def always():
        calls.append(1)
        raise Boom("503 UNAVAILABLE", 503)
    with pytest.raises(Boom):
        retry_transient(always, delays=(0, 0))
    assert len(calls) == 3                       # first try + one per delay


def test_real_errors_are_not_retried():
    calls = []

    def bad():
        calls.append(1)
        raise Boom("API key not valid", 400)
    with pytest.raises(Boom):
        retry_transient(bad, delays=(0, 0))
    assert len(calls) == 1


# ------------------------------------------------------------------ GeminiChat: retries, then the backup model
class FakeResp:
    candidates = []
    text = '{"summary_plain": "ok"}'


class FakeModels:
    def __init__(self, behaviour):
        self.behaviour, self.seen = behaviour, []

    def generate_content(self, model, **kw):
        self.seen.append(model)
        out = self.behaviour(model, len(self.seen))
        if isinstance(out, Exception):
            raise out
        return out


class FakeClient:
    def __init__(self, behaviour):
        self.models = FakeModels(behaviour)


def chat(behaviour, fallback="backup-model"):
    return GeminiChat("key", "main-model", client=FakeClient(behaviour), fallback_model=fallback)


def test_main_model_recovers_after_a_503():
    c = chat(lambda m, n: Boom("503 UNAVAILABLE", 503) if n == 1 else FakeResp())
    assert c.final_json("s", "p", {"type": "OBJECT"}) == {"summary_plain": "ok"}
    assert c._client.models.seen == ["main-model", "main-model"] and c.model_name == "main-model"


def test_backup_model_answers_when_the_main_model_keeps_failing():
    c = chat(lambda m, n: Boom("503 UNAVAILABLE", 503) if m == "main-model" else FakeResp())
    assert c.final_json("s", "p", {"type": "OBJECT"}) == {"summary_plain": "ok"}
    assert c._client.models.seen[:3] == ["main-model"] * 3 and c._client.models.seen[-1] == "backup-model"
    assert c.model_name == "backup-model"          # the run reports the model that really answered


def test_both_models_down_raises_the_error():
    c = chat(lambda m, n: Boom("503 UNAVAILABLE", 503))
    with pytest.raises(Boom):
        c.final_json("s", "p", {"type": "OBJECT"})


def test_a_real_error_does_not_switch_models():
    c = chat(lambda m, n: Boom("API key not valid", 400))
    with pytest.raises(Boom):
        c.final_json("s", "p", {"type": "OBJECT"})
    assert c._client.models.seen == ["main-model"]


def test_no_backup_model_configured():
    c = chat(lambda m, n: Boom("503 UNAVAILABLE", 503), fallback=None)
    with pytest.raises(Boom):
        c.final_json("s", "p", {"type": "OBJECT"})
    assert set(c._client.models.seen) == {"main-model"}
