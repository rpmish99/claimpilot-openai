// Thin client for the ClaimPilot API (API Gateway in AWS, scripts/local_api.py locally).
export const API_URL: string = (import.meta.env.VITE_API_URL || "http://localhost:8790").replace(/\/$/, "");

const KEY_STORAGE = "claimpilot.demoKey";

export function getDemoKey(): string {
  try {
    return localStorage.getItem(KEY_STORAGE) || "";
  } catch {
    return "";
  }
}

export function setDemoKey(k: string) {
  try {
    localStorage.setItem(KEY_STORAGE, k);
  } catch {
    /* private mode: key lives for this page only */
  }
}

export class AuthError extends Error {}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(API_URL + path, {
    ...init,
    headers: { "content-type": "application/json", "x-demo-key": getDemoKey(), ...(init.headers || {}) },
  });
  if (res.status === 401) throw new AuthError("Demo access key required");
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export type Route = "auto_accept" | "needs_review" | "return_to_provider";
export type StepState = "pending" | "running" | "done" | "error";

export interface Extracted { value: string | null; confidence: number }
export interface Finding { rule_id: string; severity: string; route: Route; field: string; message: string; cleared_by?: string | null }
export interface AgentEvent { tool: string; args: Record<string, unknown>; result: unknown; at: string }
export interface Usage { model: string; requests: number; input_tokens: number; cached_tokens: number; output_tokens: number; reasoning_tokens: number; cost_usd: number | null }
export interface Notification { audience: string; subject: string; body: string; sent_via?: string }

export interface Intake {
  intake_id: string;
  filename: string;
  source: string;
  sample?: string;
  batch_id?: string | null;
  created_at: string;
  completed_at?: string;
  status: string;
  route?: Route;
  steps: Record<string, { state: StepState; ms?: number; at?: string; route?: string; findings?: number; tool_calls?: number;
    model?: string; escalated?: boolean; escalating_to?: string }>;
  cascade?: { first_model: string; final_model: string; escalated: boolean; reasons: string[] } | null;
  extraction?: Record<string, any>;
  findings?: Finding[];
  resolution?: {
    recommendation: Route;
    patient_id: string | null;
    coverage_id: string | null;
    rendering_practitioner_id: string | null;
    claim_id: string | null;
    corrections: { field: string; original: string; corrected: string; reason: string }[];
    issues: { code: string; detail: string }[];
    processor_summary: string;
    provider_message: string;
  };
  decision_reasons?: string[];
  agent_events?: AgentEvent[];
  agent_flags?: { code: string; explanation: string }[];
  guardrail?: { problems: string[] } | null;
  claim?: Record<string, any> | null;
  claim_id?: string | null;
  usage?: Record<string, Usage>;
  cost_usd?: number;
  total_ms?: number;
  notifications?: Notification[];
  review?: { state: string; action?: string; note?: string; by?: string; at?: string } | null;
  error?: string;
  patient?: string;
  total_charge?: string;
}

export interface Batch {
  batch_id: string;
  source: string;
  created_at: string;
  completed_at?: string;
  expected: number | null;
  completed: number;
  cost_usd: number;
  counts: Record<Route | "error", number>;
  status: "processing" | "complete";
  intakes?: Intake[];
}

export interface Sample { claim_id: string; file: string; scenario: string; note: string; expected_route: Route }

export const api = {
  samples: () => call<Sample[]>("/samples"),
  list: () => call<Intake[]>("/intakes"),
  get: (id: string) => call<Intake>(`/intakes/${id}`),
  pdf: (id: string) => call<{ url: string }>(`/intakes/${id}/pdf`),
  submitSample: (sample: string) => call<{ intake_id: string }>("/intakes", { method: "POST", body: JSON.stringify({ sample }) }),
  async upload(file: File) {
    const r = await call<{ intake_id: string; upload_url: string }>("/intakes", {
      method: "POST",
      body: JSON.stringify({ filename: file.name }),
    });
    const put = await fetch(r.upload_url, { method: "PUT", body: file, headers: { "content-type": "application/pdf" } });
    if (!put.ok) throw new Error(`Upload failed: ${put.status}`);
    return r.intake_id;
  },
  batches: () => call<Batch[]>("/batches"),
  batch: (id: string) => call<Batch>(`/batches/${id}`),
  sampleBatch: () => call<{ batch_id: string; expected: number }>("/batches", { method: "POST", body: JSON.stringify({ sample: true }) }),
  async uploadBatch(files: File[]) {
    const r = await call<{ batch_id: string; uploads: { filename: string; upload_url: string }[] }>("/batches", {
      method: "POST",
      body: JSON.stringify({ files: files.map((f) => f.name) }),
    });
    // Upload in parallel, a few at a time; each object landing in S3 is its own event.
    const queue = r.uploads.map((u, i) => ({ u, f: files[i] }));
    const worker = async () => {
      for (let job = queue.shift(); job; job = queue.shift()) {
        await fetch(job.u.upload_url, { method: "PUT", body: job.f, headers: { "content-type": "application/pdf" } });
      }
    };
    await Promise.all([worker(), worker(), worker(), worker()]);
    return r.batch_id;
  },
  review: (id: string, action: "approve" | "return", note: string, provider_message?: string, corrections?: Record<string, string>) =>
    call<Intake>(`/intakes/${id}/review`, { method: "POST", body: JSON.stringify({ action, note, provider_message, corrections }) }),
};
