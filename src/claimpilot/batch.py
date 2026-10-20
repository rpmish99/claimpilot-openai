"""Batch intake: many claims dropped at once, processed independently, summarized once.

A batch is a folder of PDFs (batches/<batch_id>/*.pdf). Each file is its own event and its own
pipeline run; the batch record only counts results. Counters are atomic, and exactly one finisher
flips the batch to complete and sends the summary, however many Lambdas run in parallel.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from .notify import Notifier
from .repository import BATCH_PREFIX, Repository, new_batch_item


def new_batch_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M") + "-" + secrets.token_hex(2)


def intake_id_for_key(key: str) -> str:
    """Deterministic per S3 object, so a redelivered event never processes a claim twice."""
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def ensure_batch(repo: Repository, batch_id: str, expected: int | None, source: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    repo.put_if_absent(new_batch_item(batch_id, expected, source, now))


def set_expected(repo: Repository, notifier: Notifier, batch_id: str, expected: int) -> None:
    ensure_batch(repo, batch_id, expected, "manifest")
    batch = repo.update(BATCH_PREFIX + batch_id, expected=expected)
    _maybe_complete(repo, notifier, batch)


def finish_claim(repo: Repository, notifier: Notifier, batch_id: str, route: str, cost: float | None) -> dict:
    batch = repo.record_batch_result(batch_id, route, cost or 0)
    _maybe_complete(repo, notifier, batch)
    return batch


def _maybe_complete(repo: Repository, notifier: Notifier, batch: dict) -> None:
    expected = batch.get("expected")
    if expected and batch.get("completed", 0) >= expected and repo.mark_batch_complete(batch["batch_id"]):
        msg = notifier.send_batch_summary(batch)
        repo.update(BATCH_PREFIX + batch["batch_id"], completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    summary=msg)
