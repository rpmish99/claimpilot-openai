"""Intake records (status, trace, results, review decisions): DynamoDB in AWS, JSON files locally."""
from __future__ import annotations

import json
import os
import threading
from decimal import Decimal
from pathlib import Path
from typing import Protocol


class Repository(Protocol):
    def put(self, item: dict) -> None: ...
    def get(self, intake_id: str) -> dict | None: ...
    def update(self, intake_id: str, **fields) -> dict: ...
    def list(self, limit: int = 50) -> list[dict]: ...
    def put_if_absent(self, item: dict) -> bool: ...
    def list_batches(self, limit: int = 20) -> list[dict]: ...
    def record_batch_result(self, batch_id: str, route: str, cost: float) -> dict: ...
    def mark_batch_complete(self, batch_id: str) -> bool: ...


BATCH_PREFIX = "B#"  # batch records share the table; their key is B#<batch_id>
LABEL_PREFIX = "L#"  # processor-review labels (feedback for evals); key L#<intake_id>
ROUTES = ("auto_accept", "needs_review", "return_to_provider", "error")


def new_batch_item(batch_id: str, expected: int | None, source: str, created_at: str) -> dict:
    return {"intake_id": BATCH_PREFIX + batch_id, "kind": "batch", "batch_id": batch_id, "source": source,
            "created_at": created_at, "expected": expected, "completed": 0, "cost_usd": 0.0,
            "counts": {r: 0 for r in ROUTES}, "status": "processing"}


class LocalRepository:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def put(self, item):
        with self._lock:
            self._path(item["intake_id"]).write_text(json.dumps(item, indent=1, default=str))

    def put_if_absent(self, item):
        with self._lock:
            if self._path(item["intake_id"]).exists():
                return False
            self._path(item["intake_id"]).write_text(json.dumps(item, indent=1, default=str))
            return True

    def get(self, intake_id):
        p = self._path(intake_id)
        return json.loads(p.read_text()) if p.exists() else None

    def update(self, intake_id, **fields):
        with self._lock:
            item = self.get(intake_id) or {"intake_id": intake_id}
            item.update(fields)
            self._path(intake_id).write_text(json.dumps(item, indent=1, default=str))
            return item

    def _all(self):
        return [json.loads(p.read_text()) for p in self.root.glob("*.json")]

    def list(self, limit=50):
        items = [i for i in self._all() if not i.get("kind")]
        return sorted(items, key=lambda i: i.get("created_at", ""), reverse=True)[:limit]

    def list_kind(self, kind, limit=500):
        items = [i for i in self._all() if i.get("kind") == kind]
        return sorted(items, key=lambda i: i.get("created_at", ""), reverse=True)[:limit]

    def list_batches(self, limit=20):
        items = [i for i in self._all() if i.get("kind") == "batch"]
        return sorted(items, key=lambda i: i.get("created_at", ""), reverse=True)[:limit]

    def mark_batch_complete(self, batch_id) -> bool:
        with self._lock:
            key = BATCH_PREFIX + batch_id
            b = self.get(key)
            if b.get("status") == "complete":
                return False
            b["status"] = "complete"
            self._path(key).write_text(json.dumps(b, indent=1, default=str))
            return True

    def record_batch_result(self, batch_id, route, cost):
        with self._lock:
            key = BATCH_PREFIX + batch_id
            b = self.get(key)
            b["completed"] += 1
            b["counts"][route] = b["counts"].get(route, 0) + 1
            b["cost_usd"] = round(b["cost_usd"] + (cost or 0), 6)
            self._path(key).write_text(json.dumps(b, indent=1, default=str))
            return b

    def _path(self, intake_id):
        return self.root / f"{intake_id.replace('#', '_')}.json"


def _to_ddb(o):
    return json.loads(json.dumps(o, default=str), parse_float=Decimal)


def _from_ddb(o):
    if isinstance(o, list):
        return [_from_ddb(v) for v in o]
    if isinstance(o, dict):
        return {k: _from_ddb(v) for k, v in o.items()}
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    return o


class DynamoRepository:
    def __init__(self, table_name: str):
        import boto3
        self.table = boto3.resource("dynamodb").Table(table_name)

    def put(self, item):
        self.table.put_item(Item=_to_ddb(item))

    def put_if_absent(self, item):
        try:
            self.table.put_item(Item=_to_ddb(item), ConditionExpression="attribute_not_exists(intake_id)")
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def get(self, intake_id):
        r = self.table.get_item(Key={"intake_id": intake_id}, ConsistentRead=True)
        return _from_ddb(r["Item"]) if "Item" in r else None

    def update(self, intake_id, **fields):
        names = {f"#k{i}": k for i, k in enumerate(fields)}
        values = {f":v{i}": _to_ddb(v) for i, v in enumerate(fields.values())}
        expr = "SET " + ", ".join(f"#k{i} = :v{i}" for i in range(len(fields)))
        r = self.table.update_item(Key={"intake_id": intake_id}, UpdateExpression=expr,
                                   ExpressionAttributeNames=names, ExpressionAttributeValues=values,
                                   ReturnValues="ALL_NEW")
        return _from_ddb(r["Attributes"])

    def _scan(self):
        # Demo scale: a scan is fine. Production: GSI on (kind, created_at) / (batch_id).
        items, kwargs = [], {}
        while True:
            r = self.table.scan(**kwargs)
            items.extend(r["Items"])
            if "LastEvaluatedKey" not in r:
                break
            kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
        items = [_from_ddb(i) for i in items]
        return sorted(items, key=lambda i: i.get("created_at", ""), reverse=True)

    def list(self, limit=50):
        return [i for i in self._scan() if not i.get("kind")][:limit]

    def list_kind(self, kind, limit=500):
        return [i for i in self._scan() if i.get("kind") == kind][:limit]

    def list_batches(self, limit=20):
        return [i for i in self._scan() if i.get("kind") == "batch"][:limit]

    def record_batch_result(self, batch_id, route, cost):
        """Atomic counters: safe with many Lambdas finishing claims of one batch at once."""
        r = self.table.update_item(
            Key={"intake_id": BATCH_PREFIX + batch_id},
            UpdateExpression="ADD completed :one, cost_usd :cost, counts.#r :one",
            ExpressionAttributeNames={"#r": route},
            ExpressionAttributeValues={":one": 1, ":cost": Decimal(str(round(cost or 0, 6)))},
            ReturnValues="ALL_NEW")
        return _from_ddb(r["Attributes"])

    def mark_batch_complete(self, batch_id) -> bool:
        """True only for the one caller that flips the batch to complete (so the summary is sent once)."""
        try:
            self.table.update_item(
                Key={"intake_id": BATCH_PREFIX + batch_id}, UpdateExpression="SET #s = :c",
                ConditionExpression="#s <> :c", ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":c": "complete"})
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False


def repository_from_env() -> Repository:
    if os.environ.get("INTAKE_TABLE"):
        return DynamoRepository(os.environ["INTAKE_TABLE"])
    return LocalRepository(Path(__file__).resolve().parents[2] / ".local_store/intakes")
