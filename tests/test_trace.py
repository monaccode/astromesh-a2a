import re
import threading

import pytest

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


def test_redaccion_de_valores_y_claves_extra():
    t = Trace()
    t.add(
        "x",
        headers={"Cookie": "s=1", "Set-Cookie": "s=2", "session": "s3"},
        url="https://u:p@h.com/p?token=abc&ok=1&API_KEY=k2&sig=z",
        note=["Authorization: Bearer abc123", "Basic dXNlcjpwdw=="],
    )
    d = t.to_dict()[0]
    assert set(d["headers"].values()) == {"[redacted]"}
    assert (
        d["url"]
        == "https://[redacted]@h.com/p?token=[redacted]&ok=1&API_KEY=[redacted]&sig=[redacted]"
    )
    assert d["note"][0].endswith("Bearer [redacted]")
    assert d["note"][1] == "Basic [redacted]"
    blob = repr(d)
    for s in ("u:p", "abc", "k2", "dXNlcjpwdw"):
        assert s not in blob


def test_claves_reservadas():
    t = Trace()
    for k in ("stage", "ms"):
        with pytest.raises(ValueError, match=r"seconds|reserved"):
            t.add("x", **{k: 1})


def test_add_copia_profunda_y_fallback_repr():
    t = Trace()
    data = {"a": [1]}
    t.add("x", data=data, lock=threading.Lock())
    data["a"].append(2)
    d = t.to_dict()[0]
    assert d["data"] == {"a": [1]}
    assert isinstance(d["lock"], str)
