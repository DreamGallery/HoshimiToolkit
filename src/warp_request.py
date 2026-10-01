import time
from urllib.parse import urljoin

import requests

from src.config import (
    API_APP_ID,
    API_CLIENT_SECRET_KEY,
    API_URL,
    API_VERSION,
    MAX_RETRIES,
    validate_api_settings,
)


def send_request(url: str, headers: dict | None = None, verify: bool = True) -> requests.Response:
    """Bound both connection time and retry count; never return partial data."""
    last_error = None
    attempts = max(1, MAX_RETRIES)
    for attempt in range(attempts):
        try:
            response = requests.get(url, headers=headers, verify=verify, timeout=(10, 60))
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"Failed to download {url} after {attempts} attempts") from last_error


def request_update(db_revision: int = 0) -> bytes:
    validate_api_settings()
    if db_revision < 0:
        raise ValueError("Octo revision must be nonnegative")
    path = f"v2/pub/a/{API_APP_ID}/v/{API_VERSION}/list/{db_revision}"
    url = urljoin(API_URL.rstrip("/") + "/", path)
    headers = {
        "Accept": f"application/x-protobuf,x-octo-app/{API_APP_ID}",
        "X-OCTO-KEY": API_CLIENT_SECRET_KEY,
    }
    return send_request(url, headers=headers).content
