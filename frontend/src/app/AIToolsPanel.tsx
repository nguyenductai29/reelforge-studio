"use client";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, jsonRequest } from "../lib/api";
import { errorMessage } from "../lib/messages";
import Message from "../components/Message";
import Spinner from "../components/Spinner";
import ConfirmDialog from "../components/ConfirmDialog";
import ModuleTabs from "../components/ModuleTabs";

type Tool = { id: string; task: string; provider: string; model: string; is_enabled: boolean };
const tasks: Record<string, string> = { script: "Kịch bản", image: "Hình ảnh", video: "Video", voice: "Giọng đọc", music: "Âm nhạc" };
const videoPresets = [
  { provider: "fal", model: "fal-ai/veo3.1/fast", label: "fal · Veo 3.1 Fast" },
  { provider: "runware", model: "bytedance:seedance@2.5", label: "Runware · Seedance 2.5" },
  { provider: "replicate", model: "google/veo-3.1-fast", label: "Replicate · Veo 3.1 Fast" },
  { provider: "runway", model: "gen4.5", label: "Runway Dev · Gen-4.5 (không âm thanh)" },
  { provider: "dola", model: "seedance-2.5", label: "Dola Gateway · Seedance 2.5 (thử nghiệm)" },
];

export default function AIToolsPanel() {
  const [tools, setTools] = useState<Tool[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Tool | null>(null);
  const [preset, setPreset] = useState<(typeof videoPresets)[number] | null>(null);
  const [view, setView] = useState("tools");
  const [removing, setRemoving] = useState<Tool | null>(null);
  const load = useCallback(async () => {
    try { setTools(await api<Tool[]>("ai-tools")); setError(""); }
    catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const fields = new FormData(form);
    const body = { task: fields.get("task"), provider: fields.get("provider"), model: fields.get("model"), is_enabled: fields.get("is_enabled") === "on" };
    setBusy(true); setError("");
    try {
      await api(editing ? `ai-tools/${encodeURIComponent(editing.id)}` : "ai-tools", jsonRequest(editing ? "PUT" : "POST", body));
      setEditing(null); form.reset(); await load();
    } catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
    finally { setBusy(false); }
  }
  async function toggle(tool: Tool) {
    setBusy(true); setError("");
    try { await api(`ai-tools/${encodeURIComponent(tool.id)}`, jsonRequest("PUT", { task: tool.task, provider: tool.provider, model: tool.model, is_enabled: !tool.is_enabled })); await load(); }
    catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
    finally { setBusy(false); }
  }
  async function remove() {
    if (!removing) return;
    setBusy(true); setError("");
    try { await api(`ai-tools/${encodeURIComponent(removing.id)}`, { method: "DELETE" }); setRemoving(null); await load(); }
    catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
    finally { setBusy(false); }
  }
  return <div className="module-fill ai-module">
    <div className="module-notice"><p>Video qua fal, Runware, Replicate và Runway Dev có thể chạy khi server có API key, worker và credits. Runway cần khai báo host tải video; Dola Gateway chỉ chạy khi quản trị viên bật chế độ thử nghiệm. Các tác vụ AI khác hiện chỉ lưu cấu hình.</p></div>
    {error && <Message>{error}</Message>}
    <ModuleTabs split activeTab={view} onTabChange={setView} tabs={[
      { id: "tools", label: `Công cụ (${tools.length})`, content: <section className="card module-card"><h2>Công cụ đã cấu hình</h2><div className="module-scroll" tabIndex={0} aria-label="Danh sách công cụ AI">{loading ? <Spinner label="Đang tải công cụ…" /> : tools.length === 0 ? <p className="subtle">Chưa có công cụ. Thêm một model để chuẩn bị workflow.</p> : tools.map(tool => <div className="ai-tool-row" key={tool.id}><div><b>{tasks[tool.task]}</b><p className="subtle">{tool.provider} / {tool.model}</p><small>{tool.is_enabled ? videoPresets.some(preset => preset.provider === tool.provider && preset.model === tool.model) ? "Đã bật · Kiểm tra sẵn sàng ở workflow" : "Đã bật · Chưa có bộ thực thi" : "Đã tắt"}</small></div><div className="ai-tool-actions"><button disabled={busy} onClick={() => void toggle(tool)}>{tool.is_enabled ? "Tắt" : "Bật"}</button><button disabled={busy} onClick={() => { setEditing(tool); setView("configure"); }}>Sửa</button><button disabled={busy} onClick={() => setRemoving(tool)}>Xóa</button></div></div>)}</div></section> },
      { id: "configure", label: editing ? "Sửa công cụ" : "Thêm công cụ", content: <section className="card module-card"><h2>{editing ? "Sửa công cụ" : "Thêm công cụ"}</h2><form className="module-form" key={editing?.id ?? `new:${preset?.model ?? ""}`} onSubmit={save}><div className="module-scroll">{!editing && <div><p className="module-help">Chọn nhanh model video hỗ trợ:</p>{videoPresets.map(item => <button className="secondary-action" type="button" key={item.model} onClick={() => setPreset(item)}>{item.label}</button>)}</div>}<label>Tác vụ<select name="task" defaultValue={editing?.task ?? (preset ? "video" : "script")}>{Object.entries(tasks).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label><label>Provider<input name="provider" required maxLength={60} pattern="[a-zA-Z0-9._-]+" defaultValue={editing?.provider ?? preset?.provider ?? ""} placeholder="Ví dụ: fal" /></label><label>Model<input name="model" required maxLength={100} pattern="[a-zA-Z0-9._:/@-]+" defaultValue={editing?.model ?? preset?.model ?? ""} placeholder="Tên model do provider cung cấp" /></label><label className="ai-tool-check"><input type="checkbox" name="is_enabled" defaultChecked={editing?.is_enabled ?? true} /> Kích hoạt lựa chọn này</label></div><div className="module-actions"><button type="submit" disabled={busy}>{busy ? <Spinner label="Đang lưu…" /> : "Lưu công cụ"}</button>{editing && <button type="button" className="secondary-action" onClick={() => setEditing(null)}>Hủy sửa</button>}</div></form></section> },
    ]} />
    {removing && <ConfirmDialog title="Xóa công cụ AI?" description={`Xóa ${removing.provider} / ${removing.model} khỏi studio?`} onClose={() => setRemoving(null)} onConfirm={() => void remove()} busy={busy} />}
  </div>;
}
