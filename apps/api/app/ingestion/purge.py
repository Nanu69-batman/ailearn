"""Full removal of a resource: indexed chunks *and* its staged asset.

Deleting from the vector store was never enough. Ingestion stages every upload
as ``{resource_id}.{suffix}`` under ``UPLOAD_DIR`` because re-ingest and
``GET /resources/{id}/media`` both need the original bytes. Removing only the
chunks left the file on disk, and the media endpoint -- which resolves purely
from the filename -- kept serving it. That is how 12 staged files totalling
162 MB could contain a single live resource while 147 MB of orphans answered
``206 Partial Content``.

This module is the one supported way to take a resource out of the system. It
does not touch any retrieval gate: it calls the store's existing
``delete_resource`` and then removes the one file that resolution names. No
scoring, scope or strict-T logic lives here.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.ingestion.router import resolve_asset

log = logging.getLogger(__name__)


def purge_resource(
    resource_id: str,
    *,
    store=None,
    upload_dir: Path | None = None,
) -> dict:
    """Delete a resource's chunks and its staged asset.

    ``store`` defaults to the process-wide shared store. ``upload_dir``
    defaults to the ingestion router's ``UPLOAD_DIR``; both are injectable so
    tests never point at ``data/``.

    Order is deliberate: chunks first, then the file. A missing asset is not an
    error -- plenty of resources are documents whose bytes were never staged,
    and their purge must still succeed.

    Returns a small report rather than raising on the happy path, so a caller
    (or a cleanup script) can tell "chunks gone, no file was there" from
    "chunks gone and the orphan is gone too".
    """
    if store is None:
        from app.retrieval.registry import get_shared_store

        store = get_shared_store()

    store.delete_resource(resource_id)

    asset = resolve_asset(resource_id, upload_dir=upload_dir)
    asset_name = asset.name if asset is not None else None
    removed = False

    if asset is not None:
        try:
            # ``missing_ok`` keeps a concurrent second purge (or an operator
            # who cleaned up by hand) from failing an otherwise good delete.
            asset.unlink(missing_ok=True)
        except OSError:
            # The bytes are still on disk and still servable. Say so loudly
            # rather than returning a report that reads like success.
            log.exception(
                "purged chunks for %s but could not remove staged asset %s",
                resource_id,
                asset,
            )
            raise
        removed = not asset.exists()

    from app.ingestion import remote_assets

    remote_assets.remove_remote(resource_id)

    log.info(
        "purged resource=%s asset=%s removed=%s", resource_id, asset_name, removed
    )
    return {
        "resource_id": resource_id,
        "asset_name": asset_name,
        "asset_removed": removed,
    }