// Correction-learning loop client helper.
// Call captureEdit() from any editor surface when a user finalizes an edit
// to agent output. POSTs to /api/corrections/record which extracts a rule
// in the background.

export interface CaptureContext {
  project?: string | null;
  runId?: string | null;
  agentName?: string | null;
  token?: string | null;
}

export interface CaptureResult {
  ok: boolean;
  correctionId?: number | null;
  skipped?: string;
  error?: string;
}

export async function captureEdit(
  originalText: string,
  editedText: string,
  ctx: CaptureContext = {},
): Promise<CaptureResult> {
  if ((originalText || '') === (editedText || '')) {
    return { ok: true, skipped: 'no_change' };
  }
  try {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    const token = ctx.token ?? (typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null);
    if (token) headers.Authorization = `Bearer ${token}`;
    const r = await fetch('/api/corrections/record', {
      method: 'POST',
      headers,
      body: JSON.stringify({
        project: ctx.project ?? null,
        run_id: ctx.runId ?? null,
        agent_name: ctx.agentName ?? null,
        original: originalText,
        edited: editedText,
      }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, error: d.detail || d.error || `HTTP ${r.status}` };
    return { ok: true, correctionId: d.correction_id ?? null, skipped: d.skipped };
  } catch (e: any) {
    return { ok: false, error: e?.message || String(e) };
  }
}
