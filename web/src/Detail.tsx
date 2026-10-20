import { useEffect, useState } from "react";
import { api, type Intake, type Extracted } from "./api";
import { ROUTE_LABEL, StatusBadge } from "./App";

const STEPS: [string, string, string][] = [
  ["extract", "Extract", "Extraction Agent"],
  ["validate", "Validate", "Rules in code"],
  ["resolve", "Resolve", "Resolution Agent"],
  ["decide", "Decide", "Supervisor in code"],
  ["notify", "Notify", "SNS"],
];

type Tab = "extraction" | "validation" | "trace" | "claim" | "messages";

export function Detail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const [it, setIt] = useState<Intake | null>(null);
  const [tab, setTab] = useState<Tab>("trace");
  const [pdfUrl, setPdfUrl] = useState("");

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const d = await api.get(id);
        if (!alive) return;
        setIt(d);
        if (d.status !== "complete" && d.status !== "error") timer = setTimeout(tick, 1200);
      } catch {
        timer = setTimeout(tick, 2500);
      }
    };
    tick();
    api.pdf(id).then((r) => alive && setPdfUrl(r.url)).catch(() => {});
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [id]);

  if (!it) return <div className="card"><span className="spinner" /> Loading…</div>;
  const done = it.status === "complete";

  return (
    <div className="detail">
      <div className="detail-head">
        <div>
          <h2>{it.patient || it.filename}</h2>
          <p className="muted mono">{it.filename} · intake {it.intake_id} · {new Date(it.created_at).toLocaleTimeString()}</p>
        </div>
        <StatusBadge intake={it} />
      </div>

      <div className="timeline">
        {STEPS.map(([k, label, sub]) => {
          const s = it.steps?.[k] || { state: "pending" };
          return (
            <div key={k} className={`step ${s.state}`}>
              <div className="dot">{s.state === "running" ? <span className="spinner sm" /> : s.state === "done" ? "✓" : s.state === "error" ? "!" : ""}</div>
              <div className="step-label">{label}</div>
              <div className="step-sub">{sub}</div>
              <div className="step-meta">
                {k === "extract" && s.state === "running" && s.escalating_to ? <span className="esc">↑ re-reading with {s.escalating_to}</span> : null}
                {s.ms ? `${(s.ms / 1000).toFixed(1)}s` : ""}
                {k === "extract" && s.state === "done" && s.escalated ? <span className="esc"> · ↑ escalated</span> : null}
                {k === "validate" && s.state === "done" ? `${s.findings} finding${s.findings === 1 ? "" : "s"}` : ""}
                {k === "resolve" && s.state === "done" ? ` · ${s.tool_calls} tool calls` : ""}
              </div>
            </div>
          );
        })}
      </div>

      {it.error && <div className="error-bar">Pipeline error: {it.error}</div>}

      {done && it.route && <Decision it={it} />}
      {done && it.review?.state === "pending" && <ReviewPanel it={it} onDone={(n) => { setIt(n); onChanged(); }} />}
      {it.review?.state === "done" && (
        <div className="card review-done">
          <b>Processor decision: {it.review.action === "approve" ? "Approved" : "Returned to provider"}</b>
          {it.review.note && <span> · "{it.review.note}"</span>}
          <span className="muted"> · {it.review.by} at {it.review.at}</span>
        </div>
      )}

      <div className="tabs">
        {(["trace", "extraction", "validation", "claim", "messages"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
            {{ trace: "Agent trace", extraction: "Extraction", validation: "Validation", claim: "FHIR Claim", messages: "Messages" }[t]}
            {t === "validation" && it.findings?.length ? <span className="count">{it.findings.length}</span> : null}
            {t === "trace" && it.agent_events?.length ? <span className="count">{it.agent_events.length}</span> : null}
          </button>
        ))}
      </div>
      <div className="tab-body">
        {tab === "extraction" && <ExtractionView it={it} pdfUrl={pdfUrl} />}
        {tab === "validation" && <ValidationView it={it} />}
        {tab === "trace" && <TraceView it={it} />}
        {tab === "claim" && <Json value={it.claim} empty="No FHIR Claim was created for this intake." />}
        {tab === "messages" && <MessagesView it={it} />}
      </div>
    </div>
  );
}

function Decision({ it }: { it: Intake }) {
  const r = it.resolution;
  const cls = it.route === "auto_accept" ? "ok" : it.route === "needs_review" ? "warn" : "bad";
  return (
    <div className={`card decision ${cls}`}>
      <div className="decision-top">
        <div>
          <div className="decision-route">{ROUTE_LABEL[it.route!]}</div>
          <div className="muted">Why: {it.decision_reasons?.join(" · ")}</div>
        </div>
        <div className="decision-stats">
          <div><b>{((it.total_ms || 0) / 1000).toFixed(1)}s</b><span>end to end</span></div>
          <div><b>${(it.cost_usd || 0).toFixed(4)}</b><span>OpenAI API cost</span></div>
          <div><b>{it.claim_id || "–"}</b><span>FHIR Claim {it.claim?.status ? `(${it.claim.status})` : ""}</span></div>
        </div>
      </div>
      {r?.processor_summary && <p className="summary">{r.processor_summary}</p>}
      {!!r?.corrections?.length && (
        <div className="corrections">
          {r.corrections.map((c, i) => (
            <div key={i} className="correction">
              <span className="tag">corrected</span> <b>{c.field}</b>: <code className="strike">{c.original}</code> → <code>{c.corrected}</code>
              <span className="muted"> {c.reason}</span>
            </div>
          ))}
        </div>
      )}
      {it.cascade && (
        <div className="cascade">
          <b>Extraction cascade:</b> <code>{it.cascade.first_model}</code>
          {it.cascade.escalated ? (
            <> → escalated to <code>{it.cascade.final_model}</code> <span className="muted">because {it.cascade.reasons.join("; ")}</span></>
          ) : (
            <span className="muted"> read it cleanly; no escalation needed</span>
          )}
        </div>
      )}
      {it.guardrail && (
        <div className="guardrail">🛡 Output guardrail tripped: {it.guardrail.problems.join("; ")}</div>
      )}
      <div className="usage">
        {Object.entries(it.usage || {}).map(([k, u]) => (
          <span key={k} className="mono">
            {k}: {u.model} · {u.input_tokens.toLocaleString()} in / {u.output_tokens.toLocaleString()} out
            {u.reasoning_tokens ? ` (${u.reasoning_tokens} reasoning)` : ""} · {u.requests} req
          </span>
        ))}
      </div>
    </div>
  );
}

const CORRECTABLE: [string, string][] = [
  ["insured_id", "1a Insured ID"], ["patient_last_name", "2 Patient last"], ["patient_first_name", "2 Patient first"],
  ["patient_birth_date", "3 Patient DOB"], ["billing_provider_npi", "33a Billing NPI"], ["total_charge", "28 Total"],
];

function ReviewPanel({ it, onDone }: { it: Intake; onDone: (n: Intake) => void }) {
  const [note, setNote] = useState("");
  const original = (k: string) => ((it.extraction?.[k] as Extracted | undefined)?.value ?? "") as string;
  const [fields, setFields] = useState<Record<string, string>>(
    () => Object.fromEntries(CORRECTABLE.map(([k]) => [k, original(k)])),
  );
  const [msg, setMsg] = useState(it.resolution?.provider_message || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const act = async (action: "approve" | "return") => {
    setBusy(true);
    try {
      const corrections = Object.fromEntries(Object.entries(fields).filter(([k, v]) => v !== original(k)));
      const r: any = await api.review(it.intake_id, action, note, msg, corrections);
      if (r.error) setErr(r.error);
      else onDone(r);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card review">
      <h3>Processor review</h3>
      <label>Verify key fields against the scan (edits are saved as eval feedback)</label>
      <div className="correct-grid">
        {CORRECTABLE.map(([k, label]) => (
          <div key={k} className={fields[k] !== original(k) ? "edited" : ""}>
            <span className="muted small">{label}</span>
            <input value={fields[k]} onChange={(e) => setFields({ ...fields, [k]: e.target.value })} />
          </div>
        ))}
      </div>
      <label>Message to provider (editable)</label>
      <textarea rows={3} value={msg} onChange={(e) => setMsg(e.target.value)} />
      <label>Internal note</label>
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional" />
      {err && <div className="error-bar">{err}</div>}
      <div className="review-actions">
        <button className="primary" disabled={busy} onClick={() => act("approve")}>Approve claim</button>
        <button className="danger" disabled={busy} onClick={() => act("return")}>Return to provider</button>
      </div>
    </div>
  );
}

const FIELD_LABELS: [string, string][] = [
  ["insured_id", "1a Insured ID"], ["patient_last_name", "2 Patient last"], ["patient_first_name", "2 Patient first"],
  ["patient_birth_date", "3 Patient DOB"], ["patient_sex", "3 Sex"], ["insured_last_name", "4 Insured last"],
  ["patient_relationship", "6 Relationship"], ["insured_group_number", "11 Group"], ["plan_name", "11c Plan"],
  ["additional_claim_info", "19 Additional info"], ["federal_tax_id", "25 Tax ID"], ["patient_account_no", "26 Account"], ["total_charge", "28 Total"],
  ["billing_provider_name", "33 Billing provider"], ["billing_provider_npi", "33a Billing NPI"],
];

function Conf({ c }: { c: number }) {
  const cls = c >= 0.85 ? "ok" : c >= 0.6 ? "warn" : "bad";
  return <span className={`conf ${cls}`}>{Math.round(c * 100)}%</span>;
}

function ExtractionView({ it, pdfUrl }: { it: Intake; pdfUrl: string }) {
  const x = it.extraction;
  return (
    <div className="extraction">
      <div className="pdf-col">
        {pdfUrl && <a className="small" href={pdfUrl} target="_blank" rel="noreferrer">Open original scan ↗</a>}
        <div className="pdf">{pdfUrl ? <iframe title="claim" src={pdfUrl} /> : <span className="muted">Loading document…</span>}</div>
      </div>
      <div className="fields">
        {!x ? (
          <p className="muted">Extraction in progress…</p>
        ) : (
          <>
            <p className="muted">Quality: <b>{x.document_quality}</b>{x.extraction_notes ? ` · ${x.extraction_notes}` : ""}</p>
            <table>
              <tbody>
                {FIELD_LABELS.map(([k, label]) => {
                  const f = x[k] as Extracted;
                  return (
                    <tr key={k}>
                      <td className="muted">{label}</td>
                      <td className="mono">{f?.value ?? <i className="muted">blank</i>}</td>
                      <td>{f && <Conf c={f.confidence} />}</td>
                    </tr>
                  );
                })}
                <tr>
                  <td className="muted">21 Diagnoses</td>
                  <td className="mono">{(x.diagnosis_codes || []).join(", ")}</td>
                  <td><Conf c={x.diagnosis_confidence} /></td>
                </tr>
              </tbody>
            </table>
            <table className="lines">
              <thead><tr><th>#</th><th>DOS</th><th>CPT</th><th>Mod</th><th>Ptr</th><th>Charge</th><th>NPI</th><th></th></tr></thead>
              <tbody>
                {(x.service_lines || []).map((l: any) => (
                  <tr key={l.line_number} className="mono">
                    <td>{l.line_number}</td><td>{l.date_from}</td><td>{l.procedure_code}</td><td>{l.modifiers.join(" ")}</td>
                    <td>{l.diagnosis_pointer}</td><td>{l.charge?.toFixed(2)}</td><td>{l.rendering_npi}</td><td><Conf c={l.confidence} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}

function ValidationView({ it }: { it: Intake }) {
  if (!it.findings) return <p className="muted">Waiting for validation…</p>;
  if (!it.findings.length) return <p className="ok-text">✓ All deterministic checks passed: NPI check digits, ICD-10/CPT format, dates, totals, confidence.</p>;
  return (
    <ul className="findings">
      {it.findings.map((f, i) => (
        <li key={i} className={f.cleared_by ? "cleared" : f.route}>
          <span className="mono rule">{f.rule_id}</span>
          <span>{f.message}</span>
          {f.cleared_by ? <span className="ok-text">✓ {f.cleared_by}</span> : <span className="muted">→ {ROUTE_LABEL[f.route]}</span>}
        </li>
      ))}
    </ul>
  );
}

function TraceView({ it }: { it: Intake }) {
  const ev = it.agent_events || [];
  if (!ev.length) return <p className="muted">{it.status === "complete" ? "No tool calls." : "Waiting for the Resolution Agent…"}</p>;
  return (
    <div className="trace">
      {ev.map((e, i) => {
        const err = !!e.result && typeof e.result === "object" && "error" in (e.result as object);
        return (
          <details key={i} className={`tool ${err ? "tool-err" : ""}`} open={i === ev.length - 1 || err}>
            <summary>
              <span className="mono tool-name">{e.tool}</span>
              <span className="mono tool-args">{Object.entries(e.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join("  ")}</span>
              <span className="muted">{new Date(e.at).toLocaleTimeString()}</span>
            </summary>
            <Json value={e.result} />
          </details>
        );
      })}
      <p className="muted small">Full model-level trace (prompts, reasoning summaries, guardrails) is in the OpenAI Traces dashboard, grouped by intake id {it.intake_id}.</p>
    </div>
  );
}

function MessagesView({ it }: { it: Intake }) {
  const msgs = it.notifications || [];
  if (!msgs.length) return <p className="muted">No notifications yet.</p>;
  return (
    <div className="messages">
      {msgs.map((m, i) => (
        <div key={i} className={`message ${m.audience}`}>
          <div className="msg-head">
            <span className="tag">{m.audience}</span> <b>{m.subject}</b>
            {m.sent_via && <span className="muted"> · sent via {m.sent_via.toUpperCase()}</span>}
          </div>
          <pre>{m.body}</pre>
        </div>
      ))}
    </div>
  );
}

function Json({ value, empty }: { value: unknown; empty?: string }) {
  if (value == null) return <p className="muted">{empty || "–"}</p>;
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}
