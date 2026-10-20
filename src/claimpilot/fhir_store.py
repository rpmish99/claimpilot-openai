"""FHIR R4 data access. One small interface, two implementations.

- HealthLakeStore: AWS HealthLake over its FHIR REST API, signed with SigV4.
- LocalFhirStore:  the same seed data in memory (plus a JSON file for created claims), so the
                   pipeline and evals run on a laptop with no AWS dependency.

Only the search parameters the agent actually needs are implemented locally; they follow
standard FHIR semantics (string params are case-insensitive prefix matches, token params
accept `system|value`).
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Protocol


class FhirStore(Protocol):
    def search(self, resource_type: str, params: dict[str, str]) -> list[dict]: ...
    def read(self, resource_type: str, rid: str) -> dict | None: ...
    def create(self, resource: dict) -> dict: ...


# ---------------------------------------------------------------- local
class LocalFhirStore:
    def __init__(self, seed_path: str | Path, claims_path: str | Path | None = None):
        bundle = json.loads(Path(seed_path).read_text())
        self._db: dict[str, dict[str, dict]] = {}
        for e in bundle["entry"]:
            r = e["resource"]
            self._db.setdefault(r["resourceType"], {})[r["id"]] = r
        self._claims_path = Path(claims_path) if claims_path else None
        self._lock = threading.Lock()
        if self._claims_path and self._claims_path.exists():
            for r in json.loads(self._claims_path.read_text()):
                self._db.setdefault("Claim", {})[r["id"]] = r

    def read(self, resource_type, rid):
        return self._db.get(resource_type, {}).get(rid)

    def create(self, resource):
        r = dict(resource)
        r.setdefault("id", str(uuid.uuid4()))
        with self._lock:
            self._db.setdefault(r["resourceType"], {})[r["id"]] = r
            if self._claims_path and r["resourceType"] == "Claim":
                self._claims_path.parent.mkdir(parents=True, exist_ok=True)
                self._claims_path.write_text(json.dumps(list(self._db["Claim"].values()), indent=2))
        return r

    def search(self, resource_type, params):
        return [r for r in self._db.get(resource_type, {}).values() if all(
            _match(r, k, v) for k, v in params.items())]


def _match(r: dict, key: str, value: str) -> bool:
    v = value.lower()
    if key == "identifier":
        system, _, val = value.rpartition("|")
        return any((not system or i.get("system") == system) and i.get("value", "").lower() == val.lower()
                   for i in r.get("identifier", []))
    if key in ("family", "given", "name"):
        names = r.get("name", [])
        vals = []
        for n in names:
            if key in ("family", "name"):
                vals.append(n.get("family", ""))
            if key in ("given", "name"):
                vals.extend(n.get("given", []))
        return any(x.lower().startswith(v) for x in vals)
    if key == "birthdate":
        return r.get("birthDate") == value
    if key in ("beneficiary", "patient", "subscriber"):
        field = {"subscriber": "subscriber", "beneficiary": "beneficiary"}.get(key) or ("patient" if "patient" in r else "beneficiary")
        ref = r.get(field, {}).get("reference", "")
        return ref == value or ref.endswith("/" + value)
    if key == "_id":
        return r.get("id") == value
    if key == "status":
        return r.get("status") == value
    raise ValueError(f"Unsupported local search parameter: {key}")


# ---------------------------------------------------------------- HealthLake
class HealthLakeStore:
    def __init__(self, datastore_endpoint: str, region: str | None = None):
        import boto3  # Lambda runtime ships boto3; imported lazily so local mode needs no AWS libs
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        self.endpoint = datastore_endpoint.rstrip("/") + "/"
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")
        self._session = boto3.Session()
        self._SigV4Auth, self._AWSRequest = SigV4Auth, AWSRequest

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        url = self.endpoint + path
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}
        req = self._AWSRequest(method=method, url=url, data=data, headers=headers)
        self._SigV4Auth(self._session.get_credentials().get_frozen_credentials(), "healthlake", self.region).add_auth(req)
        http = urllib.request.Request(url, data=data, method=method, headers=dict(req.headers))
        try:
            with urllib.request.urlopen(http, timeout=20) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            raise RuntimeError(f"HealthLake {method} {path} -> {e.code}: {e.read()[:500]!r}") from e

    def search(self, resource_type, params):
        q = urllib.parse.urlencode(params)
        out = self._call("GET", f"{resource_type}?{q}")
        return [e["resource"] for e in out.get("entry", [])]

    def read(self, resource_type, rid):
        return self._call("GET", f"{resource_type}/{rid}") or None

    def create(self, resource):
        if resource.get("id"):
            return self._call("PUT", f"{resource['resourceType']}/{resource['id']}", resource)
        return self._call("POST", resource["resourceType"], resource)


def store_from_env() -> FhirStore:
    endpoint = os.environ.get("HEALTHLAKE_ENDPOINT")
    if endpoint:
        return HealthLakeStore(endpoint)
    root = Path(__file__).resolve().parents[2]
    return LocalFhirStore(root / "data/fhir/seed_bundle.json", root / ".local_store/claims.json")
