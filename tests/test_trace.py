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
        headers={"Cookie": "s=1", "Set-Cookie": "s=2", "session_token": "s3"},
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


@pytest.mark.parametrize(
    "key",
    [
        "authorization", "Proxy-Authorization", "x-api-key", "X-API-Key", "api_key", "apikey",
        "access_token", "refresh_token", "id_token", "client_secret", "secret", "password",
        "passwd", "token", "bearer", "cookie", "Set-Cookie", "session_token", "session_key",
        "session_cookie", "db_password", "github_token", "stripe_api_key",
        "stripe_secret_key", "aws_secret_access_key", "AWS-Secret-Access-Key", "ssh_private_key",
    ],
)  # fmt: skip
def test_claves_secretas_redactadas(key):
    assert Trace_dict(key) == "[redacted]"


@pytest.mark.parametrize(
    "key",
    [
        "session_id", "sessions", "context_id", "task_id", "token_count", "tokens_in",
        "tokens_out", "input_tokens", "output_tokens", "prompt_tokens", "completion_tokens",
        "total_tokens", "max_tokens", "cookie_count",
    ],
)  # fmt: skip
def test_claves_de_uso_e_ids_visibles(key):
    assert Trace_dict(key) == "v"


def Trace_dict(key):  # noqa: N802
    t = Trace()
    t.add("x", data={key: "v"})
    return t.to_dict()[0]["data"][key]


def test_query_con_prefijos():
    t = Trace()
    t.add("x", url="https://h.com/p?x=1&client_secret=abc&refresh_token=zz&id_token=q&sort_key=a")
    t.add("y", url="https://h.com/p?apikey=1&x-api-key=2&keyword=k&access_token=3&api_key=4")
    a, b = (e["url"] for e in t.to_dict())
    assert a == (
        "https://h.com/p?x=1&client_secret=[redacted]&refresh_token=[redacted]"
        "&id_token=[redacted]&sort_key=a"
    )
    assert b == (
        "https://h.com/p?apikey=[redacted]&x-api-key=[redacted]&keyword=k"
        "&access_token=[redacted]&api_key=[redacted]"
    )


def test_query_url_prefirmada():
    t = Trace()
    t.add(
        "x",
        url="https://s3.example.com/o?X-Amz-Credential=AKIA%2F1&X-Amz-Signature=abc"
        "&X-Amz-Expires=60&credential=c&goog_signature=g",
    )
    assert t.to_dict()[0]["url"] == (
        "https://s3.example.com/o?X-Amz-Credential=[redacted]&X-Amz-Signature=[redacted]"
        "&X-Amz-Expires=60&credential=[redacted]&goog_signature=[redacted]"
    )


@pytest.mark.parametrize("key", ["private_key_id", "secret_keys_count", "public_key"])
def test_sufijos_nuevos_no_tapan_vecinos(key):
    assert Trace_dict(key) == "v"
