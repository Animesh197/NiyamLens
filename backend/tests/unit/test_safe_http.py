"""Allowlist, redirect, timeout/retry and size-limit behaviour (no real network)."""

import httpx
import pytest

from app.ingestion.safe_http import FetchError, SafeHttpClient, SourcePolicyError

RBI_HOSTS = {"www.rbi.org.in", "rbi.org.in", "rbidocs.rbi.org.in"}
PAGE = "https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12953"


def make_client(handler, *, max_retries: int = 2, sleeps: list | None = None) -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=RBI_HOSTS,
        connect_timeout=1,
        read_timeout=1,
        max_retries=max_retries,
        retry_backoff=0.5,
        max_redirects=3,
        user_agent="test-agent",
        transport=httpx.MockTransport(handler),
        sleep=(sleeps.append if sleeps is not None else lambda _s: None),
    )


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>ok</html>")


def test_successful_get_uses_only_get_and_records_metadata() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok(request)

    result = make_client(handler).get(PAGE, max_bytes=1000)

    assert [r.method for r in seen] == ["GET"]
    assert seen[0].headers["user-agent"] == "test-agent"
    assert result.final_url == PAGE
    assert result.status_code == 200
    assert result.media_type == "text/html"
    assert result.content == b"<html>ok</html>"
    assert result.retrieved_at.tzinfo is not None


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/file.pdf",
        "http://www.rbi.org.in/Scripts/x.aspx",  # plain http
        "https://rbi.org.in.evil.example/x",  # lookalike suffix
        "https://evilrbi.org.in/x",  # lookalike prefix
        "https://user:pw@www.rbi.org.in/x",  # embedded credentials
        "https://www.rbi.org.in:8443/x",  # non-default port
    ],
)
def test_disallowed_urls_are_rejected_before_any_request(url: str) -> None:
    calls: list[httpx.Request] = []
    client = make_client(lambda r: calls.append(r) or ok(r))
    with pytest.raises(SourcePolicyError):
        client.get(url, max_bytes=1000)
    assert calls == []


def test_per_call_allowlist_can_only_narrow() -> None:
    client = make_client(ok)
    with pytest.raises(SourcePolicyError):
        client.get(PAGE, max_bytes=1000, allowed_hosts=["rbidocs.rbi.org.in"])
    with pytest.raises(SourcePolicyError):
        client.get("https://example.com/x", max_bytes=1000, allowed_hosts=["example.com"])


def test_allowlisted_redirect_is_followed_and_recorded() -> None:
    target = "https://rbidocs.rbi.org.in/rdocs/a.PDF"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == PAGE:
            return httpx.Response(302, headers={"location": target})
        return ok(request)

    result = make_client(handler).get(PAGE, max_bytes=1000)
    assert result.final_url == target
    assert result.redirect_chain == (PAGE,)


def test_off_domain_redirect_is_rejected_and_never_requested() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example/fake.pdf"})

    with pytest.raises(SourcePolicyError, match="Rejected redirect"):
        make_client(handler).get(PAGE, max_bytes=1000)
    assert requested == [PAGE]


def test_relative_redirect_is_resolved_against_current_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("BS_ViewMasDirections.aspx"):
            return httpx.Response(301, headers={"location": "/Scripts/Other.aspx"})
        return ok(request)

    result = make_client(handler).get(PAGE, max_bytes=1000)
    assert result.final_url == "https://www.rbi.org.in/Scripts/Other.aspx"


def test_too_many_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": PAGE})

    with pytest.raises(FetchError, match="Too many redirects"):
        make_client(handler).get(PAGE, max_bytes=1000)


def test_size_limit_is_enforced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 2000)

    with pytest.raises(FetchError, match="limit"):
        make_client(handler).get(PAGE, max_bytes=1000)


def test_transient_status_is_retried_with_bounded_backoff() -> None:
    statuses = iter([503, 502, 200])
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        return ok(request) if status == 200 else httpx.Response(status)

    result = make_client(handler, max_retries=2, sleeps=sleeps).get(PAGE, max_bytes=1000)
    assert result.status_code == 200
    assert result.attempts == 3
    assert sleeps == [0.5, 1.0]


def test_retries_are_bounded() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(FetchError, match="HTTP 503"):
        make_client(handler, max_retries=2).get(PAGE, max_bytes=1000)
    assert len(calls) == 3


def test_client_errors_are_not_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    with pytest.raises(FetchError, match="HTTP 404"):
        make_client(handler).get(PAGE, max_bytes=1000)
    assert len(calls) == 1


def test_network_errors_are_retried_then_reported() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(FetchError, match="Network error .* after 2 attempt"):
        make_client(handler, max_retries=1).get(PAGE, max_bytes=1000)
    assert len(calls) == 2
