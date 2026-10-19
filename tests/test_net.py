import httpx
import pytest

from astromesh_a2a.errors import A2AError, BlockedDestination
from astromesh_a2a.net import PinnedTransport, blocked_reason, safe_client


@pytest.mark.parametrize(
    "host",
    [
        "10.1.2.3",
        "172.16.0.1",
        "172.31.255.1",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "[::1]",
        "[fc00::1]",
        "[fd12::1]",
        "[fe80::1]",
        "[::ffff:10.0.0.1]",
        "[64:ff9b::a00:5]",
        "[::10.0.0.1]",
        "[::a00:1]",
        "[::7f00:1]",
        "224.0.0.1",
        "239.255.255.250",
        "[ff02::1]",
        "[::ffff:0:10.0.0.1]",
        "[fec0::1]",
        "localhost",
        "redis",
        "db.svc",
        "x.cluster.local",
    ],
)
def test_blocks_private_ranges(host):
    assert blocked_reason(f"http://{host}/x")


def test_allows_public_and_rejects_scheme():
    assert blocked_reason("https://8.8.8.8/") is None
    assert blocked_reason("https://example.com/") is None
    assert blocked_reason("file:///etc/passwd")
    assert blocked_reason("http:///nohost")


def fake_resolver(ip):
    calls = []

    async def resolve(host, port):
        calls.append(host)
        return [(2, 1, 6, "", (ip, port or 0))]

    resolve.calls = calls
    return resolve


def inner_ok(seen=None, **resp):
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, **resp)

    return httpx.MockTransport(handler)


class _Chunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        for _ in range(10):
            yield b"x" * 100


async def test_dns_rebinding_blocked():
    seen = []
    t = PinnedTransport(inner=inner_ok(seen), resolver=fake_resolver("10.0.0.5"))
    async with httpx.AsyncClient(transport=t) as c:
        with pytest.raises(BlockedDestination) as ei:
            await c.get("https://agent.example.com/card")
    assert seen == []
    assert t.reason == ei.value.reason
    assert "non-public" in t.reason
    assert isinstance(ei.value, A2AError)


async def test_mixed_resolution_blocked():
    async def resolve(host, port):
        return [(2, 1, 6, "", ("8.8.8.8", 0)), (2, 1, 6, "", ("10.0.0.5", 0))]

    t = PinnedTransport(inner=inner_ok(), resolver=resolve)
    async with httpx.AsyncClient(transport=t) as c:
        with pytest.raises(BlockedDestination):
            await c.get("https://agent.example.com/")


async def test_resolver_failure_fails_closed():
    async def resolve(host, port):
        raise OSError("nxdomain")

    t = PinnedTransport(inner=inner_ok(), resolver=resolve)
    async with httpx.AsyncClient(transport=t) as c:
        with pytest.raises(BlockedDestination):
            await c.get("https://agent.example.com/")


async def test_first_reason_kept():
    t = PinnedTransport(inner=inner_ok(), resolver=fake_resolver("8.8.8.8"))
    async with httpx.AsyncClient(transport=t) as c:
        with pytest.raises(BlockedDestination):
            await c.get("http://10.0.0.1/")
        first = t.reason
        with pytest.raises(BlockedDestination):
            await c.get("http://192.168.0.1/")
    assert t.reason == first


async def test_does_not_follow_redirects():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

    t = PinnedTransport(inner=httpx.MockTransport(handler), resolver=fake_resolver("8.8.8.8"))
    async with httpx.AsyncClient(transport=t, follow_redirects=False) as c:
        r = await c.get("https://agent.example.com/")
    assert r.status_code == 302
    assert len(seen) == 1
    assert "302" in t.reason


async def test_body_cap_counts_streamed_bytes():
    inner = httpx.MockTransport(lambda request: httpx.Response(200, stream=_Chunks()))
    t = PinnedTransport(max_bytes=250, inner=inner, resolver=fake_resolver("8.8.8.8"))
    async with httpx.AsyncClient(transport=t) as c:
        with pytest.raises(BlockedDestination):
            await c.get("https://agent.example.com/")
    assert "exceeds 250" in t.reason


async def test_body_under_cap_ok():
    t = PinnedTransport(
        max_bytes=250, inner=inner_ok(content=b"y" * 200), resolver=fake_resolver("8.8.8.8")
    )
    async with httpx.AsyncClient(transport=t) as c:
        r = await c.get("https://agent.example.com/")
    assert r.content == b"y" * 200
    assert t.reason is None


async def test_rejects_content_encoding():
    t = PinnedTransport(
        inner=inner_ok(headers={"content-encoding": "gzip"}, stream=_Chunks()),
        resolver=fake_resolver("8.8.8.8"),
    )
    async with httpx.AsyncClient(transport=t) as c:
        with pytest.raises(BlockedDestination):
            await c.get("https://agent.example.com/")


async def test_sni_and_host_correct():
    seen = []
    resolver = fake_resolver("93.184.216.34")
    t = PinnedTransport(inner=inner_ok(seen), resolver=resolver)
    async with httpx.AsyncClient(transport=t) as c:
        await c.get("https://agent.example.com:8443/card?a=1")
        await c.get("https://agent.example.com:8443/other")
    req = seen[0]
    assert req.url.host == "93.184.216.34"
    assert req.url.port == 8443
    assert req.headers["Host"] == "agent.example.com:8443"
    assert req.extensions["sni_hostname"] == "agent.example.com"
    assert req.headers["Accept-Encoding"] == "identity"
    assert resolver.calls == ["agent.example.com"]  # resolved once


async def test_default_port_host_header_has_no_port():
    seen = []
    t = PinnedTransport(inner=inner_ok(seen), resolver=fake_resolver("93.184.216.34"))
    async with httpx.AsyncClient(transport=t) as c:
        await c.get("https://agent.example.com/")
    assert seen[0].headers["Host"] == "agent.example.com"


async def test_single_host_per_transport():
    t = PinnedTransport(inner=inner_ok(), resolver=fake_resolver("8.8.8.8"))
    async with httpx.AsyncClient(transport=t) as c:
        await c.get("https://a.example.com/")
        with pytest.raises(BlockedDestination):
            await c.get("https://b.example.com/")


async def test_allow_private_lifts_only_address_rule():
    seen = []
    t = PinnedTransport(inner=inner_ok(seen), allow_private=True)
    async with httpx.AsyncClient(transport=t) as c:
        r = await c.get("http://127.0.0.1:9/x")
    assert r.status_code == 200
    assert len(seen) == 1


async def test_safe_client_config():
    c = safe_client(timeout=7.0, max_bytes=10)
    try:
        assert c.follow_redirects is False
        assert c.timeout.read == 7.0
        with pytest.raises(BlockedDestination):
            await c.get("http://127.0.0.1:1/")
    finally:
        await c.aclose()


async def test_dns_ipv4_compatible_blocked():
    seen = []
    t = PinnedTransport(inner=inner_ok(seen), resolver=fake_resolver("::a00:5"))
    async with httpx.AsyncClient(transport=t) as c:
        with pytest.raises(BlockedDestination):
            await c.get("https://agent.example.com/")
    assert seen == []


async def test_concurrent_first_requests_one_host_only():
    import asyncio

    async def slow(host, port):
        await asyncio.sleep(0.01)
        return [(2, 1, 6, "", ("8.8.8.8", 0))]

    t = PinnedTransport(inner=inner_ok(), resolver=slow)
    async with httpx.AsyncClient(transport=t) as c:
        res = await asyncio.gather(
            c.get("https://a.example.com/"),
            c.get("https://b.example.com/"),
            return_exceptions=True,
        )
    assert sum(isinstance(r, BlockedDestination) for r in res) == 1
    assert sum(isinstance(r, httpx.Response) for r in res) == 1


async def test_response_keeps_hostname():
    t = PinnedTransport(inner=inner_ok(), resolver=fake_resolver("8.8.8.8"))
    async with httpx.AsyncClient(transport=t) as c:
        r = await c.get("https://agent.example.com/x")
    assert r.request.url.host == "agent.example.com"
    assert r.url.host == "agent.example.com"


async def test_safe_client_rejects_bypass_kwargs():
    for kw in ({"mounts": {}}, {"proxy": "http://p:1"}):
        with pytest.raises(ValueError, match="does not accept"):
            safe_client(timeout=1.0, max_bytes=1, **kw)
