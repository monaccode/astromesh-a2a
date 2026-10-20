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
    m.admit()
    m.admit()
    with pytest.raises(PolicyRejected, match="budget"):
        m.admit()
    assert m.calls == 2  # a refused call is not counted
    unlimited = BudgetMeter(None)
    for _ in range(5):
        unlimited.admit()
    assert unlimited.remaining_seconds() is None


def test_presupuesto_segundos():
    m = BudgetMeter(Budget(max_seconds=1.0))
    m.admit()
    m.record_seconds(0.6)
    assert m.remaining_seconds() == pytest.approx(0.4)
    m.admit()  # some seconds are left: admitted
    m.record_seconds(0.6)  # the call already happened: recorded, never raises
    assert m.remaining_seconds() == 0.0
    with pytest.raises(PolicyRejected, match="budget"):
        m.admit()


def test_meter_sin_metodos_legados():
    assert not hasattr(BudgetMeter, "charge_call")
    assert not hasattr(BudgetMeter, "charge_seconds")


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


def test_puerto_se_ignora():
    Policy(allowed=("a.com",)).check_card_url("https://a.com:8443/x")


@pytest.mark.parametrize(
    "url",
    ["https://user@a.com/x", "https://user:pw@a.com/x", "https://:pw@a.com/", "https://@a.com/"],
)
def test_userinfo_rechazado(url):
    # Credentials never ride in the URL (they would leak into logs and the card fetch).
    with pytest.raises(PolicyRejected) as exc:
        Policy(allowed=("a.com",)).check_card_url(url)
    assert exc.value.reason == "url"


def test_str_pelado_rechazado():
    with pytest.raises(TypeError):
        Policy(allowed="a.com")
    with pytest.raises(TypeError):
        Policy(accept_parts="text")


def test_segundos_negativos_y_tope():
    m = BudgetMeter(Budget(max_seconds=1.0))
    m.record_seconds(-1)  # clamped: negative time never refunds the budget
    assert m.seconds == 0.0
    m.record_seconds(1.0)
    with pytest.raises(PolicyRejected):
        m.admit()


def test_max_calls_cero_rechaza_la_primera():
    with pytest.raises(PolicyRejected):
        BudgetMeter(Budget(max_calls=0)).admit()
