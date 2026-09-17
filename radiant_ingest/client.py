"""HTTP/GraphQL client for RateMyProfessors with retry and backoff.

This is an unofficial, undocumented API. Transient failures (timeouts, 5xx,
Cloudflare HTML in place of JSON) are expected and retried. Schema drift is
also expected but is *not* retried: a GraphQL ``errors`` field means the query
no longer matches the schema, so we log it in full and stop rather than
persisting partial or garbage data.
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Optional

import requests

from . import config

log = logging.getLogger(__name__)


class IngestError(RuntimeError):
    """Ingestion failed in a way the caller should not paper over."""


class GraphQLError(IngestError):
    """The API returned a GraphQL ``errors`` field, or a body with no ``data``.

    Fatal by design. Continuing past this would write partial or garbage rows
    into the raw dataset the rating engine is built on.
    """


class RmpClient:
    """Thin GraphQL client. Reuses one connection and counts requests made."""

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(config.HEADERS)
        self.request_count = 0

    def __enter__(self) -> "RmpClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.session.close()

    def execute(
        self,
        query: str,
        variables: dict[str, Any],
        *,
        op_name: str = "query",
    ) -> dict[str, Any]:
        """POST a GraphQL operation and return its ``data`` object.

        Retries transient transport failures with exponential backoff + jitter.
        Raises :class:`GraphQLError` on schema drift (no retry) and
        :class:`IngestError` on non-retryable HTTP status or exhausted retries.
        """
        payload = {"query": query, "variables": variables}
        last_err: Optional[Exception] = None

        for attempt in range(config.MAX_RETRIES + 1):
            if attempt:
                delay = min(config.BACKOFF_BASE * 2 ** (attempt - 1), config.BACKOFF_MAX)
                delay += random.uniform(0, delay * 0.25)  # jitter, avoid lockstep retries
                log.warning(
                    "%s: attempt %d/%d failed (%s) — retrying in %.1fs",
                    op_name, attempt, config.MAX_RETRIES, last_err, delay,
                )
                time.sleep(delay)

            try:
                resp = self.session.post(
                    config.GRAPHQL_URL, json=payload, timeout=config.REQUEST_TIMEOUT
                )
                self.request_count += 1
            except requests.RequestException as exc:
                last_err = exc
                continue

            if resp.status_code in config.RETRYABLE_STATUS:
                last_err = IngestError(f"HTTP {resp.status_code}")
                retry_after = _retry_after_seconds(resp)
                if retry_after is not None:
                    log.warning(
                        "%s: HTTP %d with Retry-After %.0fs — honoring it",
                        op_name, resp.status_code, retry_after,
                    )
                    time.sleep(retry_after)
                continue

            if resp.status_code != 200:
                raise IngestError(
                    f"{op_name}: HTTP {resp.status_code} is not retryable.\n"
                    f"{resp.text[:2000]}"
                )

            try:
                body = resp.json()
            except ValueError:
                # Almost always an HTML error page or a Cloudflare challenge.
                last_err = IngestError(f"non-JSON response: {resp.text[:300]!r}")
                continue

            if body.get("errors"):
                log.error(
                    "%s: GraphQL errors returned — STOPPING (schema drift?).\n%s",
                    op_name, json.dumps(body["errors"], indent=2),
                )
                raise GraphQLError(
                    f"{op_name} returned GraphQL errors: {json.dumps(body['errors'])}"
                )

            data = body.get("data")
            if data is None:
                raise GraphQLError(
                    f"{op_name}: response carried neither `data` nor `errors`: "
                    f"{json.dumps(body)[:1000]}"
                )
            return data

        raise IngestError(
            f"{op_name}: gave up after {config.MAX_RETRIES} retries; last error: {last_err}"
        ) from last_err


def _retry_after_seconds(resp: requests.Response) -> Optional[float]:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        # Only the delta-seconds form; an HTTP-date here would be unusual.
        return max(0.0, min(float(raw), config.BACKOFF_MAX))
    except ValueError:
        return None
