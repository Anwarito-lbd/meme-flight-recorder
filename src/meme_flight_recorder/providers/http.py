from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def get_json(
    base_url: str,
    path: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
    retries: int = 3,
) -> Any:
    query = urllib.parse.urlencode(params or {}, doseq=True)
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}" + (f"?{query}" if query else "")
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "meme-flight-recorder/0.1",
            **(headers or {}),
        },
        method="GET",
    )
    return _open_json(request, timeout, retries)


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = 20,
    retries: int = 2,
) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "user-agent": "meme-flight-recorder/0.3",
            **(headers or {}),
        },
        method="POST",
    )
    return _open_json(request, timeout, retries)


def _open_json(request: urllib.request.Request, timeout: int, retries: int) -> Any:
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            retryable = error.code == 429 or 500 <= error.code < 600
            if not retryable or attempt >= retries:
                raise
            retry_after = error.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 2**attempt
            except ValueError:
                delay = 2**attempt
            time.sleep(min(max(delay, 0.25), 10))
    raise RuntimeError("HTTP retry loop ended unexpectedly")
