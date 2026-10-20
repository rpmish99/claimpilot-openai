"""Processor and provider notifications. SNS email in AWS; recorded on the intake record either way."""
from __future__ import annotations

import os

from .schemas import ResolutionResult, Route

ROUTE_LABEL = {Route.auto_accept: "Auto-accepted", Route.needs_review: "Needs review",
               Route.return_to_provider: "Returned to provider"}


class Notifier:
    def __init__(self, topic_arn: str | None = None):
        self.topic_arn = topic_arn
        self._sns = None
        if topic_arn:
            import boto3
            self._sns = boto3.client("sns")

    def send(self, intake_id: str, route: Route, res: ResolutionResult | None, claim: dict | None) -> list[dict]:
        summary = res.processor_summary if res else "The agent did not return a summary; see the trace."
        msgs = [{
            "audience": "processor",
            "subject": f"[ClaimPilot] {ROUTE_LABEL[route]}: intake {intake_id}",
            "body": f"{summary}\n\nFHIR Claim: {claim['id'] + ' (' + claim['status'] + ')' if claim else 'not created'}",
        }]
        if res and res.provider_message and route != Route.auto_accept:
            msgs.append({"audience": "provider",
                         "subject": f"Cascadia Health Plan: action needed on claim intake {intake_id}",
                         "body": res.provider_message})
        if self._sns:
            for m in msgs:
                # Demo: one topic, so both audiences go to the demo inbox. Production: provider portal / EDI 277CA.
                self._sns.publish(TopicArn=self.topic_arn, Subject=m["subject"][:99],
                                  Message=f"To: {m['audience']}\n\n{m['body']}")
                m["sent_via"] = "sns"
        return msgs

    def send_batch_summary(self, batch: dict) -> dict:
        c = batch.get("counts", {})
        body = (f"Batch {batch['batch_id']} is complete: {batch['completed']} claims.\n"
                f"  Auto-accepted:        {c.get('auto_accept', 0)}\n"
                f"  Needs review:         {c.get('needs_review', 0)}\n"
                f"  Returned to provider: {c.get('return_to_provider', 0)}\n"
                f"  Errors:               {c.get('error', 0)}\n"
                f"OpenAI API cost: ${batch.get('cost_usd', 0):.4f}\n\n"
                "Open the ClaimPilot work queue to review the exceptions.")
        msg = {"audience": "processor", "subject": f"[ClaimPilot] Batch {batch['batch_id']} complete", "body": body}
        if self._sns:
            self._sns.publish(TopicArn=self.topic_arn, Subject=msg["subject"][:99], Message=body)
            msg["sent_via"] = "sns"
        return msg


def notifier_from_env() -> Notifier:
    return Notifier(os.environ.get("SNS_TOPIC_ARN"))
