"""The deterministic supervisor.

The sequence of steps is fixed here, in code. The agents never hand off to each other or choose
the next step, and the supervisor, not a model, makes the final routing decision:

    EXTRACT (Extraction Agent) -> VALIDATE (code) -> RESOLVE (Resolution Agent) -> DECIDE (code) -> NOTIFY
"""
from __future__ import annotations

import os
import time
import traceback
from datetime import datetime, timezone

from agents import OutputGuardrailTripwireTriggered, RunConfig, Runner, trace
from openai import AsyncOpenAI

from . import config
from .corroborate import corroborate
from .extraction_agent import build_extraction_agent, escalation_reasons, extraction_input
from .fhir_store import FhirStore
from .notify import Notifier
from .repository import Repository
from .resolution_agent import ClaimContext, build_resolution_agent, resolution_input
from .schemas import ClaimExtraction, ResolutionResult, Route
from .validators import route_from_findings, validate

SEVERITY = {Route.auto_accept: 0, Route.needs_review: 1, Route.return_to_provider: 2}
STEPS = ["extract", "validate", "resolve", "decide", "notify"]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _usage(result, model: str) -> dict:
    u = result.context_wrapper.usage
    cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
    reasoning = getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", 0) or 0
    return {"model": model, "requests": u.requests, "input_tokens": u.input_tokens, "cached_tokens": cached,
            "output_tokens": u.output_tokens, "reasoning_tokens": reasoning,
            "cost_usd": config.cost_usd(model, u.input_tokens, cached, u.output_tokens)}


def new_intake(repo: Repository, intake_id: str, filename: str, source: str, **extra) -> dict:
    item = {"intake_id": intake_id, "filename": filename, "source": source, "created_at": _now(),
            "status": "queued", "steps": {s: {"state": "pending"} for s in STEPS}, **extra}
    repo.put(item)
    return item


def run_pipeline(intake_id: str, pdf_bytes: bytes, filename: str, *, store: FhirStore, repo: Repository,
                 notifier: Notifier, extraction_override: ClaimExtraction | None = None) -> dict:
    steps = (repo.get(intake_id) or {}).get("steps") or {s: {"state": "pending"} for s in STEPS}
    t0 = time.perf_counter()

    def step(name, state, **info):
        steps[name] = {**steps.get(name, {}), "state": state, "at": _now(), **info}
        repo.update(intake_id, steps=steps, status=name if state == "running" else repo_status(state, name))

    def repo_status(state, name):
        return "error" if state == "error" else name

    run_cfg = RunConfig(workflow_name="ClaimPilot intake", group_id=intake_id,
                        trace_include_sensitive_data=config.TRACE_SENSITIVE,
                        trace_metadata={"intake_id": intake_id, "file": filename})
    usage = {}
    cascade = None
    doc = extraction_input(pdf_bytes, filename) if extraction_override is None else None
    strong = config.ESCALATION_MODEL if config.ESCALATION_MODEL and config.ESCALATION_MODEL != config.EXTRACTION_MODEL else None

    def extract(force_strong: bool) -> ClaimExtraction:
        nonlocal cascade
        step("extract", "running", **({"escalating_to": strong} if force_strong else {}))
        ts = time.perf_counter()
        if extraction_override is not None:
            step("extract", "done", ms=0, model="override", escalated=False)
            return extraction_override
        if force_strong:
            r = Runner.run_sync(build_extraction_agent(AsyncOpenAI(), strong, config.ESCALATION_EFFORT), doc, run_config=run_cfg)
            extraction = r.final_output_as(ClaimExtraction)
            usage["escalation"] = _usage(r, strong)
        else:
            r = Runner.run_sync(build_extraction_agent(AsyncOpenAI()), doc, run_config=run_cfg)
            extraction = r.final_output_as(ClaimExtraction)
            usage["extraction"] = _usage(r, config.EXTRACTION_MODEL)
            cascade = {"first_model": config.EXTRACTION_MODEL, "final_model": config.EXTRACTION_MODEL,
                       "escalated": False, "reasons": []}
            why = escalation_reasons(extraction, validate(extraction))
            if why and strong:
                cascade.update(escalated=True, reasons=why, final_model=strong, first_pass=extraction.model_dump(mode="json"))
                step("extract", "running", escalating_to=strong)
                r = Runner.run_sync(build_extraction_agent(AsyncOpenAI(), strong, config.ESCALATION_EFFORT), doc, run_config=run_cfg)
                extraction = r.final_output_as(ClaimExtraction)
                usage["escalation"] = _usage(r, strong)
        repo.update(intake_id, cascade=cascade, extraction=extraction.model_dump(mode="json"))
        step("extract", "done", ms=int((time.perf_counter() - ts) * 1000), model=cascade["final_model"],
             escalated=cascade["escalated"])
        return extraction

    def resolve_and_decide(extraction: ClaimExtraction, usage_key: str):
        # 2. VALIDATE (deterministic)
        step("validate", "running")
        findings = validate(extraction)
        step("validate", "done", findings=len(findings))
        repo.update(intake_id, findings=[f.model_dump(mode="json") for f in findings])

        # 3. RESOLVE
        step("resolve", "running")
        ts = time.perf_counter()
        ctx = ClaimContext(intake_id=intake_id, store=store, extraction=extraction, findings=findings)
        guardrail = None
        resolution: ResolutionResult | None = None
        try:
            r = Runner.run_sync(build_resolution_agent(AsyncOpenAI()), resolution_input(extraction, findings), context=ctx,
                                max_turns=12, run_config=run_cfg)
            resolution = r.final_output_as(ResolutionResult)
            usage[usage_key] = _usage(r, config.RESOLUTION_MODEL)
        except OutputGuardrailTripwireTriggered as e:
            guardrail = e.guardrail_result.output.output_info
            resolution = e.guardrail_result.agent_output
        step("resolve", "done", ms=int((time.perf_counter() - ts) * 1000), tool_calls=len(ctx.events))

        # 4. DECIDE (deterministic: most severe of validators, agent, guardrail)
        step("decide", "running")
        reasons = []
        findings = corroborate(findings, extraction, store, resolution.patient_id if resolution else None,
                               resolution.coverage_id if resolution else None)
        cleared = [f for f in findings if f.cleared_by]
        if cleared:
            reasons.append(f"{len(cleared)} low-confidence field(s) verified against FHIR records")
            repo.update(intake_id, findings=[f.model_dump(mode="json") for f in findings])
        route = route_from_findings(findings)
        if route != Route.auto_accept:
            reasons.append(f"validators: {route.value}")
        if resolution and SEVERITY[resolution.recommendation] > SEVERITY[route]:
            route = resolution.recommendation
            reasons.append(f"agent: {resolution.recommendation.value}")
        if guardrail:
            route = max(route, Route.needs_review, key=SEVERITY.get)
            reasons.append(f"guardrail tripped: {'; '.join(guardrail['problems'])}")
        first_quality = ((cascade or {}).get("first_pass") or {}).get("document_quality")
        poor_scan = extraction.document_quality != "clean" or first_quality not in (None, "clean")
        if route == Route.return_to_provider and poor_scan:
            route = Route.needs_review
            reasons.append("poor-quality scan: a processor confirms before anything is returned to the provider")
        if any(f["code"] == "DUPLICATE" for f in ctx.flags):
            route = max(route, Route.needs_review, key=SEVERITY.get)
            reasons.append("possible duplicate of a claim on file")
        if route == Route.auto_accept and not ctx.created_claim_id:
            route = Route.needs_review
            reasons.append("no claim created")
        if route == Route.auto_accept:
            reasons.append("all checks passed")
        return ctx, resolution, guardrail, route, reasons

    try:
        with trace("ClaimPilot intake", group_id=intake_id, metadata={"intake_id": intake_id, "file": filename}):
            # 1. EXTRACT (fast model; escalates on poor scans, low confidence or rule failures)
            extraction = extract(force_strong=False)
            ctx, resolution, guardrail, route, reasons = resolve_and_decide(extraction, "resolution")

            # Confirm-before-reject: a claim never goes back to the provider on the fast model's read alone.
            if (route == Route.return_to_provider and cascade and not cascade["escalated"] and strong
                    and not ctx.created_claim_id):
                cascade.update(escalated=True, final_model=strong, first_pass=extraction.model_dump(mode="json"),
                               reasons=["confirming a return to provider with the stronger model"],
                               first_pass_route=route.value, first_pass_tool_calls=ctx.events)
                extraction = extract(force_strong=True)
                ctx, resolution, guardrail, route, reasons = resolve_and_decide(extraction, "resolution_recheck")
                reasons.insert(0, "re-read with the stronger model before returning to provider")

            claim = ctx.created_claim
            want = "active" if route == Route.auto_accept else "draft"
            if claim and claim.get("status") != want:
                claim = {**claim, "status": want}
                store.create(claim)
            step("decide", "done", route=route.value)

            # 5. NOTIFY
            step("notify", "running")
            msgs = notifier.send(intake_id, route, resolution, claim)
            step("notify", "done")

        total_ms = int((time.perf_counter() - t0) * 1000)
        cost = sum((u.get("cost_usd") or 0) for u in usage.values())
        return repo.update(
            intake_id, status="complete", completed_at=_now(), route=route.value, decision_reasons=reasons,
            resolution=resolution.model_dump(mode="json") if resolution else None,
            agent_events=ctx.events, agent_flags=ctx.flags, claim=claim, claim_id=ctx.created_claim_id,
            guardrail=guardrail, usage=usage, cost_usd=round(cost, 5), total_ms=total_ms,
            notifications=msgs, review={"state": "pending"} if route != Route.auto_accept else None,
        )
    except Exception as e:  # noqa: BLE001 - supervisor must always land the record
        running = next((s for s, v in steps.items() if v.get("state") == "running"), None)
        if running:
            steps[running] = {**steps[running], "state": "error"}
        repo.update(intake_id, status="error", steps=steps, error=f"{type(e).__name__}: {e}",
                    error_trace=traceback.format_exc()[-2000:], usage=usage)
        raise
    finally:
        _flush_traces()


def _flush_traces():
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        try:
            from agents.tracing import get_trace_provider
            get_trace_provider().force_flush()
        except Exception:  # noqa: BLE001
            pass
