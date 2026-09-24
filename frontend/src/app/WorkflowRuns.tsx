"use client";
import { useCallback, useEffect, useState } from "react";
import Spinner from "../components/Spinner";
import Message from "../components/Message";
import { api, jsonRequest } from "../lib/api";
import { errorMessage } from "../lib/messages";
import YouTubePublish from "./YouTubePublish";

type Step = { node_id: string; node_type: string; status: string; detail: string; output: Record<string, unknown> | null };
type Run = { id: string; workflow_id: string; project_id: string; retry_of_id: string | null; status: string; created_at: string; steps?: Step[] };
type Project = { id: string; title: string; topic?: string };
type VideoTool = { id: string; task: string; provider: string; model: string; is_enabled: boolean };
type Readiness = { runnable: boolean; credits_required: number; credits_available: number; steps: { task: string; status: string; detail: string }[] };
const labels: Record<string, string> = { completed: "Hoàn thành", blocked: "Cần xử lý", skipped: "Đang chờ", failed: "Lỗi", needs_attention: "Cần đối soát", running: "Đang chạy", queued: "Trong hàng đợi", submitting: "Đang gửi", awaiting_review: "Chờ duyệt" };
const nodeLabels: Record<string, string> = { idea: "Ý tưởng", script: "Kịch bản", scenes: "Phân cảnh", image: "Hình ảnh", video: "Video", assets: "Kho media", voice: "Giọng đọc", music: "Âm nhạc", subtitle: "Phụ đề", render: "Render", review: "Duyệt", publish: "Đăng tải" };

export default function WorkflowRuns({ workflowId, projects, hasVideo, dirty, onInspect }: { workflowId: string; projects: Project[]; hasVideo: boolean; dirty: boolean; onInspect: (steps: Step[] | null) => void }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<Run | null>(null);
  const [projectId, setProjectId] = useState(projects[0]?.id ?? "");
  const [prompt, setPrompt] = useState(projects[0]?.topic ?? "");
  const [videoTools, setVideoTools] = useState<VideoTool[]>([]);
  const [toolId, setToolId] = useState("");
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setRuns(await api<Run[]>(`workflows/${encodeURIComponent(workflowId)}/runs`)); setError(""); }
    catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
    finally { setLoading(false); }
  }, [workflowId]);
  useEffect(() => { void load(); return () => onInspect(null); }, [load, onInspect]);
  useEffect(() => {
    if (!hasVideo) return;
    void api<VideoTool[]>("ai-tools").then(tools => setVideoTools(tools.filter(tool => tool.task === "video" && tool.is_enabled))).catch(e => setError(errorMessage(e instanceof Error ? e.message : String(e))));
  }, [hasVideo]);
  useEffect(() => {
    if (!hasVideo) return;
    const query = toolId ? `?tool_id=${encodeURIComponent(toolId)}` : "";
    void api<Readiness>(`workflows/${encodeURIComponent(workflowId)}/readiness${query}`).then(setReadiness).catch(e => setError(errorMessage(e instanceof Error ? e.message : String(e))));
  }, [workflowId, toolId, hasVideo, runs.length]);
  useEffect(() => {
    if (!selected || selected.status !== "running") return;
    const id = selected.id;
    const timer = window.setInterval(() => {
      void api<Run>(`workflow-runs/${encodeURIComponent(id)}`).then(run => {
        setSelected(run); onInspect(run.steps ?? []); void load();
      }).catch(e => setError(errorMessage(e instanceof Error ? e.message : String(e))));
    }, 5000);
    return () => window.clearInterval(timer);
  }, [selected?.id, selected?.status, load, onInspect]);
  async function inspect(id: string) {
    setError("");
    try { const run = await api<Run>(`workflow-runs/${encodeURIComponent(id)}`); setSelected(run); onInspect(run.steps ?? []); }
    catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
  }
  async function start(retryId?: string) {
    setBusy(true); setError("");
    try {
      const run = await api<Run>(retryId ? `workflow-runs/${encodeURIComponent(retryId)}/retry` : `workflows/${encodeURIComponent(workflowId)}/runs`, retryId ? { method: "POST" } : jsonRequest("POST", { project_id: projectId, prompt: prompt.trim() || null, tool_id: toolId || null }));
      await load(); setSelected(run); onInspect(run.steps ?? []);
    } catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
    finally { setBusy(false); }
  }
  async function approve(id: string) {
    setBusy(true); setError("");
    try {
      const run = await api<Run>(`workflow-runs/${encodeURIComponent(id)}/approve`, { method: "POST" });
      setSelected(run); onInspect(run.steps ?? []); await load();
    } catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
    finally { setBusy(false); }
  }
  const approvedVideoStep = selected?.steps?.find(step => step.node_type === "video" && typeof step.output?.asset_id === "string");
  const reviewStep = selected?.steps?.find(step => step.node_type === "review");
  const approvedAssetId = reviewStep?.status === "completed" && typeof reviewStep.output?.approved_by === "string" ? approvedVideoStep?.output?.asset_id : null;
  return <section className="card run-panel"><div className="card-head"><div><h2>Lịch sử chạy workflow</h2><p className="subtle">Workflow mẫu tạo một clip từ chủ đề qua model được hỗ trợ, lưu MP4 vào kho media rồi chờ duyệt. Các node khác sẽ dừng khi chưa có bộ thực thi.</p></div><span className="tag">{runs.length} LẦN CHẠY GẦN NHẤT</span></div>
    {error && <Message>{error}</Message>}
    <div className="run-controls"><label>Dự án<select value={projectId} onChange={e => { setProjectId(e.target.value); setPrompt(projects.find(project => project.id === e.target.value)?.topic ?? ""); }} disabled={busy}>{projects.length ? projects.map(project => <option key={project.id} value={project.id}>{project.title}</option>) : <option value="">Tạo dự án trước</option>}</select></label><button type="button" className="primary-action" disabled={busy || !projectId || dirty} onClick={() => void start()}>{busy ? <Spinner label="Đang xử lý…" /> : "▶ Chạy workflow"}</button></div>
    {hasVideo && <><label>Model video<select value={toolId} onChange={e => setToolId(e.target.value)}><option value="">Tự động chọn model đầu tiên</option>{videoTools.map(tool => <option key={tool.id} value={tool.id}>{tool.provider} · {tool.model}</option>)}</select></label><label>Prompt video<textarea value={prompt} maxLength={3000} onChange={e => setPrompt(e.target.value)} placeholder="Để trống để dùng chủ đề hoặc tên dự án" /></label>{readiness && <p className="module-help">{readiness.steps.find(step => step.task === "video")?.detail} · Credits: {readiness.credits_available} / {readiness.credits_required}</p>}</>}
    <div className="module-scroll" tabIndex={0} aria-label="Kết quả chạy workflow">
    {dirty && <p className="hint">Lưu sơ đồ trước khi chạy để kết quả khớp với các node đang thấy.</p>}
    {loading ? <Spinner label="Đang tải lịch sử…" /> : !runs.length ? <p className="subtle">Chưa có lần chạy. Chọn dự án và bắt đầu.</p> : <div className="run-history">{runs.map(run => <button type="button" key={run.id} className={selected?.id === run.id ? "active" : ""} onClick={() => void inspect(run.id)}><b>{labels[run.status] ?? run.status}</b><small>{new Date(run.created_at).toLocaleString("vi-VN")} · {projects.find(p => p.id === run.project_id)?.title ?? "Dự án"}</small>{run.retry_of_id && <small>Chạy lại</small>}</button>)}</div>}
    {selected && <div className="run-detail"><div className="card-head"><h2>Chi tiết lần chạy</h2>{selected.status === "awaiting_review" && <button className="primary-action" type="button" disabled={busy} onClick={() => void approve(selected.id)}>✓ Duyệt video</button>}{["blocked", "failed"].includes(selected.status) && reviewStep?.status !== "completed" && <button className="secondary-action" type="button" disabled={busy || dirty} onClick={() => void start(selected.id)}>↻ Chạy lại</button>}</div>{selected.status === "needs_attention" && <p className="hint">Yêu cầu tạo video cần được đối soát với provider. Credit vẫn được giữ; hãy liên hệ quản trị viên trước khi tạo video khác.</p>}<p className="subtle">{selected.retry_of_id ? "Chạy lại từ bản sơ đồ của lần trước. " : ""}Video được lưu riêng trong kho media sau khi hoàn thành.</p><div className="run-steps">{selected.steps?.map(step => <div key={step.node_id} className={`run-step run-${step.status}`}><span className="run-dot"/><div><b>{nodeLabels[step.node_type] ?? step.node_type}</b><small>{step.node_id} · {labels[step.status] ?? step.status}</small><p>{step.detail}</p>{step.node_type === "video" && typeof step.output?.asset_id === "string" && <><video controls playsInline preload="metadata" src={`/api/assets/${encodeURIComponent(step.output.asset_id)}`} style={{ width: "100%", maxWidth: 360 }} />{approvedAssetId === step.output.asset_id && <p><a href={`/api/assets/${encodeURIComponent(step.output.asset_id)}`}>Tải MP4 để đăng thủ công ↗</a></p>}</>}{step.output && <details><summary>Xem dữ liệu đầu ra</summary><pre>{JSON.stringify(step.output, null, 2)}</pre></details>}</div></div>)}</div>{typeof approvedAssetId === "string" && <YouTubePublish key={selected.id} runId={selected.id} assetId={approvedAssetId} defaultTitle={projects.find(project => project.id === selected.project_id)?.title ?? "Video"} />}</div>}
    </div>
  </section>;
}
