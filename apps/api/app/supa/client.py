"""Thin httpx client for Supabase PostgREST and Storage (docs/SUPABASE_PLAN.md).

Server side only: it authenticates with the service-role key, which bypasses
RLS, so every caller must scope rows by ``owner_id`` itself. No ``supabase``
SDK dependency.

``get_client()`` returns ``None`` unless ``settings.supabase_enabled()``, so
callers write ``if (c := get_client()) is not None`` and the app behaves
exactly as before without Supabase. Tests inject a client built on
``httpx.MockTransport`` with ``set_client()``; nothing here touches the
network on import.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings

log = logging.getLogger(__name__)

TIMEOUT_S = 10.0
#: Uploads can be large videos; give them their own, longer budget.
UPLOAD_TIMEOUT_S = 300.0


class SupabaseError(RuntimeError):
    """A non-2xx reply from PostgREST or Storage."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"supabase {status}: {detail}")
        self.status = status
        self.detail = detail


class SupabaseClient:
    def __init__(
        self,
        url: str,
        service_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.url,
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
            },
            timeout=TIMEOUT_S,
            transport=transport,
        )

    @staticmethod
    def _check(res: httpx.Response) -> httpx.Response:
        if res.status_code >= 400:
            raise SupabaseError(res.status_code, res.text[:500])
        return res

    # -- PostgREST ---------------------------------------------------------

    def select(
        self, table: str, filters: Mapping[str, str] | None = None, columns: str = "*"
    ) -> list[dict[str, Any]]:
        """``filters`` are PostgREST operators, e.g. ``{"owner_id": "eq.<uuid>"}``."""
        params = {"select": columns, **(filters or {})}
        return self._check(self._http.get(f"/rest/v1/{table}", params=params)).json()

    def upsert(
        self,
        table: str,
        rows: dict[str, Any] | Iterable[dict[str, Any]],
        on_conflict: str | None = None,
    ) -> None:
        body = [rows] if isinstance(rows, dict) else list(rows)
        if not body:
            return
        params = {"on_conflict": on_conflict} if on_conflict else None
        self._check(
            self._http.post(
                f"/rest/v1/{table}",
                json=body,
                params=params,
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )
        )

    def delete(self, table: str, filters: Mapping[str, str]) -> None:
        # An empty filter would delete the whole table; refuse it outright.
        if not filters:
            raise ValueError("delete() requires at least one filter")
        self._check(self._http.delete(f"/rest/v1/{table}", params=dict(filters)))

    # -- Storage -----------------------------------------------------------

    def _object_url(self, bucket: str, path: str) -> str:
        return f"/storage/v1/object/{quote(bucket)}/{quote(path)}"

    def upload(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
        self._check(
            self._http.post(
                self._object_url(bucket, path),
                content=data,
                headers={"Content-Type": content_type, "x-upsert": "true"},
                timeout=UPLOAD_TIMEOUT_S,
            )
        )

    def download(self, bucket: str, path: str) -> bytes:
        res = self._http.get(self._object_url(bucket, path), timeout=UPLOAD_TIMEOUT_S)
        return self._check(res).content

    def remove(self, bucket: str, paths: list[str]) -> None:
        if not paths:
            return
        self._check(
            self._http.request(
                "DELETE", f"/storage/v1/object/{quote(bucket)}", json={"prefixes": paths}
            )
        )

    def signed_url(self, bucket: str, path: str, expires_in: int = 3600) -> str:
        res = self._check(
            self._http.post(
                f"/storage/v1/object/sign/{quote(bucket)}/{quote(path)}",
                json={"expiresIn": expires_in},
            )
        )
        return f"{self.url}/storage/v1{res.json()['signedURL']}"


_client: SupabaseClient | None = None
_client_lock = threading.Lock()


def get_client() -> SupabaseClient | None:
    """The process-wide client, or ``None`` when Supabase is not configured."""
    global _client
    if _client is not None:
        return _client
    if not settings.supabase_enabled():
        return None
    with _client_lock:
        if _client is None:
            _client = SupabaseClient(
                settings.supabase_url, settings.supabase_service_role_key
            )
    return _client


def set_client(client: SupabaseClient | None) -> None:
    """Test seam: inject a MockTransport-backed client, or ``None`` to reset."""
    global _client
    _client = client
