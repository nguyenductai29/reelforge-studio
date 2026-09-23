"use client";
import { useCallback, useEffect, useState } from "react";
import Spinner from "../components/Spinner";
import Message from "../components/Message";
import { api, jsonRequest } from "../lib/api";
import { errorMessage } from "../lib/messages";

type Step = { node_id: string; node_type: string; status: string; detail: string; output: Record<string, unknown> | null };
type Run = { id: string; workflow_id: string; project_id: string; retry_of_id: string | null; status: string; created_at: string; steps?: Step[] };
type Project = { id: string; title: string };
const labels: Record<string, string> = { completed: "Hoàn thành", blocked: "Cần xử lý", skipped: "Đang chờ", failed: "Lỗi", running: "Đang chạy" };
const nodeLabels: Record<string, string> = { idea: "Ý tưởng", script: "Kịch bản", scenes: "Phân cảnh", image: "Hình ảnh", video: "Video", assets: "Kho media", voice: "Giọng đọc", music: "Âm nhạc", subtitle: "Phụ đề", render: "Render", review: "Duyệt", publish: "Đăng tải" };

export default function WorkflowRuns({ workflowId, projects, dirty, onInspect }: { workflowId: string; projects: Project[]; dirty: boolean; onInspect: (steps: Step[] | null) => void }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<Run | null>(null);
  const [projectId, setProjectId] = useState(projects[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setRuns(await api<Run[]>(`workflows/${encodeURIComponent(workflowId)}/runs`)); setError(""); }
    catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
    finally { setLoading(false); }
  }, [workflowId]);
  useEffect(() => { void load(); return () => onInspect(null); }, [load, onInspect]);
  async function inspect(id: string) {
    setError("");
    try { const run = await api<Run>(`workflow-runs/${encodeURIComponent(id)}`); setSelected(run); onInspect(run.steps ?? []); }
    catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
  }
  async function start(retryId?: string) {
    setBusy(true); setError("");
    try {
      const run = await api<Run>(retryId ? `workflow-runs/${encodeURIComponent(retryId)}/retry` : `workflows/${encodeURIComponent(workflowId)}/runs`, retryId ? { method: "POST" } : jsonRequest("POST", { project_id: projectId }));
      await load(); setSelected(run); onInspect(run.steps ?? []);
    } catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
    finally { setBusy(false); }
  }
  return <section className="card run-panel"><div className="card-head"><div><h2>Lịch sử chạy workflow</h2><p className="subtle">Chạy theo thứ tự node và lưu kết quả vào PostgreSQL. AI, render và đăng tải sẽ dừng khi chưa có bộ thực thi.</p></div><span className="tag">{runs.length} LẦN CHẠY GẦN NHẤT</span></div>
    {error && <Message>{error}</Message>}
    <div className="run-controls"><label>Dự án<select value={projectId} onChange={e => setProjectId(e.target.value)} disabled={busy}>{projects.length ? projects.map(project => <option key={project.id} value={project.id}>{project.title}</option>) : <option value="">Tạo dự án trước</option>}</select></label><button type="button" className="primary-action" disabled={busy || !projectId || dirty} onClick={() => void start()}>{busy ? <Spinner label="Đang xử lý…" /> : "▶ Chạy workflow"}</button></div>
    <div className="module-scroll" tabIndex={0} aria-label="Kết quả chạy workflow">
    {dirty && <p className="hint">Lưu sơ đồ trước khi chạy để kết quả khớp với các node đang thấy.</p>}
    {loading ? <Spinner label="Đang tải lịch sử…" /> : !runs.length ? <p className="subtle">Chưa có lần chạy. Chọn dự án và bắt đầu.</p> : <div className="run-history">{runs.map(run => <button type="button" key={run.id} className={selected?.id === run.id ? "active" : ""} onClick={() => void inspect(run.id)}><b>{labels[run.status] ?? run.status}</b><small>{new Date(run.created_at).toLocaleString("vi-VN")} · {projects.find(p => p.id === run.project_id)?.title ?? "Dự án"}</small>{run.retry_of_id && <small>Chạy lại</small>}</button>)}</div>}
    {selected && <div className="run-detail"><div className="card-head"><h2>Chi tiết lần chạy</h2>{selected.status === "blocked" && <button className="secondary-action" type="button" disabled={busy || dirty} onClick={() => void start(selected.id)}>↻ Chạy lại</button>}</div><p className="subtle">{selected.retry_of_id ? "Chạy lại từ bản sơ đồ của lần trước. " : ""}Chỉ các bước hoàn thành mới có dữ liệu đầu ra.</p><div className="run-steps">{selected.steps?.map(step => <div key={step.node_id} className={`run-step run-${step.status}`}><span className="run-dot"/><div><b>{nodeLabels[step.node_type] ?? step.node_type}</b><small>{step.node_id} · {labels[step.status] ?? step.status}</small><p>{step.detail}</p>{step.output && <details><summary>Xem dữ liệu đầu ra</summary><pre>{JSON.stringify(step.output, null, 2)}</pre></details>}</div></div>)}</div></div>}
    </div>
  </section>;
}
