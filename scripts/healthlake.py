"""Manage the AWS HealthLake FHIR datastore (separate lifecycle from the SAM stack).

    python scripts/healthlake.py create     # starts creation (~20-30 min), prints the datastore id
    python scripts/healthlake.py status     # shows status + endpoint; writes HEALTHLAKE_* to .env when ACTIVE
    python scripts/healthlake.py load       # loads data/fhir/seed_bundle.json (PUT, idempotent)
    python scripts/healthlake.py delete     # deletes the datastore (stops the ~$0.27/hr charge)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import boto3
from dotenv import load_dotenv, set_key

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ENV)

NAME = "claimpilot"
hl = boto3.client("healthlake", region_name="us-east-1")


def find():
    for d in hl.list_fhir_datastores(Filter={"DatastoreName": NAME})["DatastorePropertiesList"]:
        if d["DatastoreStatus"] not in ("DELETED", "DELETING"):
            return d
    return None


def create():
    d = find()
    if d:
        print(f"Already exists: {d['DatastoreId']} ({d['DatastoreStatus']})")
        return
    r = hl.create_fhir_datastore(DatastoreName=NAME, DatastoreTypeVersion="R4",
                                 Tags=[{"Key": "project", "Value": "claimpilot"}])
    print(f"Creating {r['DatastoreId']} ({r['DatastoreStatus']}). Run `status` in ~20 minutes.")


def status(wait: bool = False):
    while True:
        d = find()
        if not d:
            print("No datastore.")
            return
        print(f"{d['DatastoreId']}  {d['DatastoreStatus']}  {d['DatastoreEndpoint']}")
        if d["DatastoreStatus"] == "ACTIVE":
            set_key(str(ENV), "HEALTHLAKE_DATASTORE_ID", d["DatastoreId"])
            set_key(str(ENV), "HEALTHLAKE_ENDPOINT", d["DatastoreEndpoint"])
            print("Wrote HEALTHLAKE_DATASTORE_ID and HEALTHLAKE_ENDPOINT to .env")
            return
        if not wait:
            return
        time.sleep(60)


def load():
    from claimpilot.fhir_store import HealthLakeStore
    d = find()
    assert d and d["DatastoreStatus"] == "ACTIVE", "Datastore is not ACTIVE yet"
    store = HealthLakeStore(d["DatastoreEndpoint"])
    bundle = json.loads((ROOT / "data/fhir/seed_bundle.json").read_text())
    for i, e in enumerate(bundle["entry"], 1):
        store.create(e["resource"])
        print(f"\r{i}/{len(bundle['entry'])} {e['request']['url']:<40}", end="", flush=True)
    print("\nLoaded. Spot check:", len(store.search("Coverage", {"identifier":
          "https://cascadiahealth.example/member-id|CHP400172305"})), "coverage(s) for CHP400172305")


def delete():
    d = find()
    if not d:
        print("No datastore.")
        return
    hl.delete_fhir_datastore(DatastoreId=d["DatastoreId"])
    print(f"Deleting {d['DatastoreId']}.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"create": create, "status": lambda: status("--wait" in sys.argv), "load": load, "delete": delete}[cmd]()
