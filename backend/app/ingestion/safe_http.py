"""Read-only, allowlisted HTTP GET client (implementationplan.md §2.4).

- Only GET is ever issued.
- Only https URLs on allowlisted hosts are requested. Redirects are followed
  manually so that every redirect target is checked *before* it is requested.
- Finite connect/read timeouts, a bounded number of retries for transient
  failures (network errors, 429, 5xx), and a hard response-size limit.
"""

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urljoin

import httpx

from app.ingestion.manifest import check_url

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class SourcePolicyError(Exception):
    """A URL or redirect target violates the source allowlist policy."""


class FetchError(Exception):
    """The request failed (network error, bad status, or size limit)."""


@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str | None
    content: bytes
    retrieved_at: datetime
    redirect_chain: tuple[str, ...] = field(default_factory=tuple)
    attempts: int = 1

    @property
    def media_type(self) -> str | None:
        if not self.content_type:
            return None
        return self.content_type.split(";", 1)[0].strip().lower()


class SafeHttpClient:
    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str],
        connect_timeout: float,
        read_timeout: float,
        max_retries: int,
        retry_backoff: float,
        max_redirects: int,
        user_agent: str,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts)
        if not self.allowed_hosts:
            raise ValueError("allowed_hosts must not be empty")
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.max_redirects = max_redirects
        self._sleep = sleep
        self._client = httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            headers={"User-Agent": user_agent},
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SafeHttpClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def check_url(self, url: str, allowed_hosts: Iterable[str] | None = None) -> None:
        hosts = self.allowed_hosts
        if allowed_hosts is not None:
            hosts = hosts & frozenset(h.lower() for h in allowed_hosts)
        try:
            check_url(url, hosts)
        except ValueError as exc:
            raise SourcePolicyError(str(exc)) from exc

    def get(
        self, url: str, *, max_bytes: int, allowed_hosts: Iterable[str] | None = None
    ) -> FetchResult:
        """GET ``url`` following only allowlisted redirects."""
        self.check_url(url, allowed_hosts)
        current = url
        chain: list[str] = []
        total_attempts = 0
        for _ in range(self.max_redirects + 1):
            status, headers, body, attempts = self._get_once(current, max_bytes)
            total_attempts += attempts
            if status in REDIRECT_STATUSES:
                location = headers.get("location")
                if not location:
                    raise FetchError(f"HTTP {status} redirect without Location from {current}")
                target = urljoin(current, location)
                try:
                    self.check_url(target, allowed_hosts)
                except SourcePolicyError as exc:
                    raise SourcePolicyError(
                        f"Rejected redirect from {current} to {target}: {exc}"
                    ) from exc
                chain.append(current)
                current = target
                continue
            if not 200 <= status < 300:
                raise FetchError(f"HTTP {status} from {current}")
            return FetchResult(
                requested_url=url,
                final_url=current,
                status_code=status,
                content_type=headers.get("content-type"),
                content=body,
                retrieved_at=datetime.now(UTC),
                redirect_chain=tuple(chain),
                attempts=total_attempts,
            )
        raise FetchError(f"Too many redirects (> {self.max_redirects}) starting at {url}")

    def _get_once(self, url: str, max_bytes: int) -> tuple[int, httpx.Headers, bytes, int]:
        """One logical GET with bounded retries for transient failures."""
        attempt = 0
        while True:
            attempt += 1
            try:
                with self._client.stream("GET", url) as response:
                    status = response.status_code
                    headers = response.headers
                    if status in RETRYABLE_STATUSES and attempt <= self.max_retries:
                        self._sleep(self.retry_backoff * attempt)
                        continue
                    if status in REDIRECT_STATUSES or not 200 <= status < 300:
                        return status, headers, b"", attempt
                    declared = headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        raise FetchError(
                            f"Response from {url} declares {declared} bytes, "
                            f"over the {max_bytes}-byte limit"
                        )
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise FetchError(
                                f"Response from {url} exceeded the {max_bytes}-byte limit"
                            )
                    return status, headers, bytes(body), attempt
            except httpx.TransportError as exc:
                if attempt <= self.max_retries:
                    self._sleep(self.retry_backoff * attempt)
                    continue
                raise FetchError(
                    f"Network error fetching {url} after {attempt} attempt(s): "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
