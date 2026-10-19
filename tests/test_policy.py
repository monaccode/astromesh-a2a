import pytest

from astromesh_a2a import Budget, BudgetMeter, Policy, PolicyRejected
from astromesh_a2a.errors import A2AError


def test_default_no_permite_nada():
    p = Policy()
    assert p.allowed == ()
    assert p.max_bytes == 1_048_576
    assert p.timeout == 60.0
    assert p.max_events == 1000
    assert p.budget is None
    assert not p.require_signature
    with pytest.raises(PolicyRejected):
        p.check_card_url("https://agent.example.com/.well-known/agent-card.json")


def test_permitidos_por_host_y_por_url():
    url = "https://other.example.org/card.json"
    p = Policy(allowed=("Agent.Example.com", url))
    p.check_card_url("https://agent.example.com/x")
    p.check_card_url(url)
    with pytest.raises(PolicyRejected):
        p.check_card_url("https://other.example.org/different.json")
    with pytest.raises(PolicyRejected):
        p.check_card_url("https://evil.com/x")


def test_http_solo_con_opt_in():
    with pytest.raises(PolicyRejected):
        Policy(allowed=("a.com",)).check_card_url("http://a.com/x")
    Policy(allowed=("a.com",), allow_insecure_http=True).check_card_url("http://a.com/x")


def test_partes_file_rechazadas_por_defecto():
    p = Policy()
    p.check_part("text", 10)
    p.check_part("data", 10)
    with pytest.raises(PolicyRejected):
        p.check_part("file", 1)
    with pytest.raises(PolicyRejected):
        p.check_part("text", p.max_bytes + 1)


def test_presupuesto_llamadas():
    m = BudgetMeter(Budget(max_calls=2))
    m.charge_call()
    m.charge_call()
    with pytest.raises(PolicyRejected, match="budget"):
        m.charge_call()
    BudgetMeter(None).charge_call()


def test_presupuesto_segundos():
    m = BudgetMeter(Budget(max_seconds=1.0))
    m.charge_seconds(0.6)
    with pytest.raises(PolicyRejected):
        m.charge_seconds(0.6)


def test_errores_son_a2a_error():
    assert issubclass(PolicyRejected, A2AError)


async def test_client_helper_usa_safe_client():
    c = Policy(timeout=5.0).client()
    assert c.timeout.read == 5.0
    await c.aclose()


@pytest.mark.parametrize(
    "bad",
    ["https://[::1/", " https://a.com/x", "https://a.com/x\n", "https://a.com/\tx", "", "https://"],
)
def test_url_malformada_es_policy_rejected(bad):
    with pytest.raises(PolicyRejected):
        Policy(allowed=("a.com", "::1")).check_card_url(bad)


@pytest.mark.parametrize(
    "tricky",
    [
        "https://a.com.evil.com/x",
        "https://a.com@evil.com/x",
        "https://a.com:pw@evil.com/x",
        "https://evil.com/@a.com",
        "https://a.com./x",
        "https://sub.a.com/x",
    ],
)
def test_trucos_de_host_rechazados(tricky):
    with pytest.raises(PolicyRejected):
        Policy(allowed=("a.com",)).check_card_url(tricky)


def test_puerto_y_userinfo_legitimo_se_ignoran():
    p = Policy(allowed=("a.com",))
    p.check_card_url("https://a.com:8443/x")
    p.check_card_url("https://user@a.com/x")


def test_str_pelado_rechazado():
    with pytest.raises(TypeError):
        Policy(allowed="a.com")
    with pytest.raises(TypeError):
        Policy(accept_parts="text")


def test_segundos_negativos_y_tope():
    m = BudgetMeter(Budget(max_seconds=1.0))
    with pytest.raises(ValueError, match=r"seconds|reserved"):
        m.charge_seconds(-1)
    m.charge_seconds(1.0)
    with pytest.raises(PolicyRejected):
        m.charge_seconds(0.01)


def test_max_calls_cero_rechaza_la_primera():
    with pytest.raises(PolicyRejected):
        BudgetMeter(Budget(max_calls=0)).charge_call()
