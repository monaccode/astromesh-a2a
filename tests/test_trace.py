import re

from astromesh_a2a import Trace


def test_trace_entries():
    t = Trace()
    t.add("card", url="https://a.com")
    t.add("send")
    d = t.to_dict()
    assert [e["stage"] for e in d] == ["card", "send"]
    assert "ms" in d[0]
    assert d[0]["url"] == "https://a.com"


def test_trace_no_contiene_secretos():
    t = Trace()
    t.add(
        "send",
        headers={"Authorization": "Bearer sekret1", "X-API-Key": "sekret2", "Accept": "x"},
        api_key="sekret3",
        nested={"deep": [{"Password": "sekret4"}]},
    )
    d = t.to_dict()
    assert "sekret" not in repr(d)
    assert d[0]["headers"]["Accept"] == "x"
    assert d[0]["headers"]["Authorization"] == "[redacted]"


def test_traceparent_formato_w3c():
    t = Trace()
    a, b = t.traceparent(), t.traceparent()
    pat = r"00-[0-9a-f]{32}-[0-9a-f]{16}-01"
    assert re.fullmatch(pat, a)
    assert re.fullmatch(pat, b)
    assert a.split("-")[1] == b.split("-")[1]
    assert a.split("-")[2] != b.split("-")[2]
