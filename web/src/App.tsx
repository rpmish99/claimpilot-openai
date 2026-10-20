import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, AuthError, getDemoKey, setDemoKey, type Batch, type Intake, type Route, type Sample } from "./api";
import { Detail } from "./Detail";

export const ROUTE_LABEL: Record<Route, string> = {
  auto_accept: "Auto-accepted",
  needs_review: "Needs review",
  return_to_provider: "Returned to provider",
};

// Value-model assumptions, stated on screen (see docs/value_model.md).
const MANUAL_MIN_PER_CLAIM = 8;
const REVIEW_MIN_PER_EXCEPTION = 3;

type Filter = "all" | Route | "open";

export default function App() {
  const [intakes, setIntakes] = useState<Intake[]>([]);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [needKey, setNeedKey] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [batchList, setBatchList] = useState<Batch[]>([]);
  const [batchFilter, setBatchFilter] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [list, bl] = await Promise.all([api.list(), api.batches()]);
      setIntakes(list);
      setBatchList(bl);
      setErr("");
    } catch (e) {
      if (e instanceof AuthError) setNeedKey(true);
      else setErr(String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    api.samples().then(setSamples).catch((e) => e instanceof AuthError && setNeedKey(true));
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [refresh]);

  const submitSample = async (file: string) => {
    setBusy(file);
    try {
      const { intake_id } = await api.submitSample(file);
      setSelected(intake_id);
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy("");
    }
  };

  const runSampleBatch = async () => {
    setBusy("batch");
    try {
      const { batch_id } = await api.sampleBatch();
      setBatchFilter(batch_id);
      setSelected(null);
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy("");
    }
  };

  const uploadMany = async (files: File[]) => {
    setBusy("batch");
    try {
      const id = await api.uploadBatch(files);
      setBatchFilter(id);
      setSelected(null);
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy("");
    }
  };

  const upload = async (file: File) => {
    setBusy(file.name);
    try {
      const id = await api.upload(file);
      setSelected(id);
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy("");
    }
  };

  const shown = useMemo(
    () =>
      intakes.filter(
        (i) =>
          (!batchFilter || i.batch_id === batchFilter) &&
          (filter === "all" ? true : filter === "open" ? i.review?.state === "pending" : i.route === filter),
      ),
    [intakes, filter, batchFilter],
  );

  if (needKey) return <KeyGate onDone={() => { setNeedKey(false); refresh(); api.samples().then(setSamples); }} />;

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <span className="logo">✓</span>
          <div>
            <h1>ClaimPilot</h1>
            <p>Cascadia Health Plan · Claims Intake</p>
          </div>
        </div>
        <ValueStrip intakes={batchFilter ? intakes.filter((i) => i.batch_id === batchFilter) : intakes} scope={batchFilter ? "Batch" : "All intakes"} />
        <div className="powered">OpenAI Agents SDK · AWS HealthLake</div>
      </header>

      <main className="layout">
        <aside className="sidebar">
          <section className="card">
            <h2>New intake</h2>
            <DropZone onFiles={(fs) => (fs.length > 1 ? uploadMany(fs) : upload(fs[0]))} busy={!!busy} />
            <button className="batch-btn" disabled={!!busy} onClick={runSampleBatch}>
              {busy === "batch" ? <span className="spinner sm" /> : "⇶"} Drop all {samples.length || 16} samples as one batch
            </button>
            <div className="samples">
              {samples.map((s) => (
                <button key={s.file} className="sample" disabled={!!busy} onClick={() => submitSample(s.file)} title={s.note}>
                  <span className="sample-id">{s.claim_id}</span>
                  <span className="sample-name">{s.scenario.replace(/_/g, " ")}</span>
                  {busy === s.file && <span className="spinner" />}
                </button>
              ))}
            </div>
          </section>

          {batchList.length > 0 && (
            <section className="card batches">
              <h2>Batches</h2>
              {batchList.slice(0, 4).map((b) => (
                <BatchRow key={b.batch_id} b={b} active={batchFilter === b.batch_id}
                  onClick={() => setBatchFilter(batchFilter === b.batch_id ? null : b.batch_id)} />
              ))}
            </section>
          )}

          <section className="card queue">
            <div className="queue-head">
              <h2>Work queue{batchFilter && <span className="muted small"> · batch {batchFilter} <a href="#" onClick={(e) => { e.preventDefault(); setBatchFilter(null); }}>show all</a></span>}</h2>
              <select value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
                <option value="all">All</option>
                <option value="open">Open reviews</option>
                <option value="auto_accept">Auto-accepted</option>
                <option value="needs_review">Needs review</option>
                <option value="return_to_provider">Returned</option>
              </select>
            </div>
            {shown.length === 0 && <p className="muted">No intakes yet. Pick a sample claim above.</p>}
            <ul>
              {shown.map((i) => (
                <li key={i.intake_id} className={selected === i.intake_id ? "active" : ""} onClick={() => setSelected(i.intake_id)}>
                  <div className="q-main">
                    <span className="q-name">{i.patient || i.filename}</span>
                    <StatusBadge intake={i} />
                  </div>
                  <div className="q-sub">
                    <span>{i.batch_id && !batchFilter ? "⇶ " : ""}{i.filename.replace(/\.pdf$/, "")}</span>
                    <span>{i.total_charge ? `$${i.total_charge}` : ""}</span>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        </aside>

        <section className="content">
          {err && <div className="error-bar">{err}</div>}
          {selected ? (
            <Detail key={selected} id={selected} onChanged={refresh} />
          ) : (
            <Empty />
          )}
        </section>
      </main>
    </div>
  );
}

export function StatusBadge({ intake }: { intake: Intake }) {
  if (intake.status === "error") return <span className="badge err">Error</span>;
  if (!intake.route) return <span className="badge run"><span className="spinner sm" />{intake.status}</span>;
  const reviewed = intake.review?.state === "done";
  const cls = intake.route === "auto_accept" ? "ok" : intake.route === "needs_review" ? "warn" : "bad";
  return (
    <span className={`badge ${reviewed ? "done" : cls}`}>
      {reviewed ? (intake.review?.action === "approve" ? "Approved" : "Returned") : ROUTE_LABEL[intake.route]}
    </span>
  );
}

function ValueStrip({ intakes, scope }: { intakes: Intake[]; scope: string }) {
  const done = intakes.filter((i) => i.status === "complete");
  const auto = done.filter((i) => i.route === "auto_accept").length;
  const avgSec = done.length ? done.reduce((a, i) => a + (i.total_ms || 0), 0) / done.length / 1000 : 0;
  const avgCost = done.length ? done.reduce((a, i) => a + (i.cost_usd || 0), 0) / done.length : 0;
  const saved = done.length * MANUAL_MIN_PER_CLAIM - (done.length - auto) * REVIEW_MIN_PER_EXCEPTION;
  return (
    <div className="value-strip" title={`Assumes ${MANUAL_MIN_PER_CLAIM} min manual keying per claim and ${REVIEW_MIN_PER_EXCEPTION} min review per exception`}>
      <Metric label={`${scope}: processed`} value={String(done.length)} />
      <Metric label="Auto-accepted" value={done.length ? `${Math.round((auto / done.length) * 100)}%` : "–"} />
      <Metric label="Avg time / claim" value={done.length ? `${avgSec.toFixed(0)}s` : "–"} />
      <Metric label="Avg API $ / claim" value={done.length ? `$${avgCost.toFixed(3)}` : "–"} />
      <Metric label="Processor minutes saved*" value={done.length ? String(Math.max(0, saved)) : "–"} />
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span className="m-val">{value}</span>
      <span className="m-label">{label}</span>
    </div>
  );
}

function BatchRow({ b, active, onClick }: { b: Batch; active: boolean; onClick: () => void }) {
  const total = b.expected || b.completed || 1;
  const pct = Math.min(100, Math.round((b.completed / total) * 100));
  const c = b.counts || ({} as Batch["counts"]);
  return (
    <div className={`batch-row ${active ? "active" : ""}`} onClick={onClick} title="Filter the work queue to this batch">
      <div className="batch-top">
        <span className="mono">{b.batch_id}</span>
        <span className="muted small">
          {b.status === "complete" ? `done · $${(b.cost_usd || 0).toFixed(3)}` : `${b.completed}/${b.expected ?? "?"}`}
        </span>
      </div>
      <div className="bar"><div className={`fill ${b.status}`} style={{ width: `${pct}%` }} /></div>
      <div className="batch-counts">
        <span className="ok-text">{c.auto_accept || 0} accepted</span>
        <span className="warn-text">{c.needs_review || 0} review</span>
        <span className="bad-text">{c.return_to_provider || 0} returned</span>
        {c.error ? <span className="bad-text">{c.error} error</span> : null}
      </div>
    </div>
  );
}

function DropZone({ onFiles, busy }: { onFiles: (f: File[]) => void; busy: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      className={`drop ${over ? "over" : ""}`}
      onClick={() => ref.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const fs = Array.from(e.dataTransfer.files).filter((f) => f.type === "application/pdf" || f.name.endsWith(".pdf"));
        if (fs.length) onFiles(fs);
      }}
    >
      <input ref={ref} type="file" accept="application/pdf" multiple hidden
        onChange={(e) => e.target.files?.length && onFiles(Array.from(e.target.files))} />
      {busy ? <span className="spinner" /> : <>Drop CMS-1500 PDFs (one, or a whole batch) or <u>browse</u></>}
      <small>or run a synthetic sample:</small>
    </div>
  );
}

function Empty() {
  return (
    <div className="empty">
      <h2>How each claim is processed</h2>
      <ol>
        <li><b>Extract</b>: the Extraction Agent reads the scanned CMS-1500 into a strict schema</li>
        <li><b>Validate</b>: deterministic rules check NPIs, codes, dates and totals</li>
        <li><b>Resolve</b>: the Resolution Agent matches member, coverage and providers in FHIR and creates the Claim</li>
        <li><b>Decide</b>: code, not a model, routes the claim: auto-accept, review or return</li>
        <li><b>Notify</b>: processor summary and provider correction request</li>
      </ol>
      <p className="muted">Synthetic data only. Pick a sample claim to start.</p>
    </div>
  );
}

function KeyGate({ onDone }: { onDone: () => void }) {
  const [k, setK] = useState(getDemoKey());
  return (
    <div className="gate">
      <form
        className="card"
        onSubmit={(e) => {
          e.preventDefault();
          setDemoKey(k.trim());
          onDone();
        }}
      >
        <h2>ClaimPilot demo</h2>
        <p className="muted">Enter the demo access key.</p>
        <input autoFocus type="password" value={k} onChange={(e) => setK(e.target.value)} placeholder="Access key" />
        <button className="primary" type="submit">Continue</button>
      </form>
    </div>
  );
}
