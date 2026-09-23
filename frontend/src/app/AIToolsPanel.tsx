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

export default function AIToolsPanel() {
  const [tools, setTools] = useState<Tool[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Tool | null>(null);
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
    <div className="module-notice"><p>Cấu hình model cho từng tác vụ. Kết nối API và chạy AI sẽ được bổ sung sau.</p></div>
    {error && <Message>{error}</Message>}
    <ModuleTabs split activeTab={view} onTabChange={setView} tabs={[
      { id: "tools", label: `Công cụ (${tools.length})`, content: <section className="card module-card"><h2>Công cụ đã cấu hình</h2><div className="module-scroll" tabIndex={0} aria-label="Danh sách công cụ AI">{loading ? <Spinner label="Đang tải công cụ…" /> : tools.length === 0 ? <p className="subtle">Chưa có công cụ. Thêm một model để chuẩn bị workflow.</p> : tools.map(tool => <div className="ai-tool-row" key={tool.id}><div><b>{tasks[tool.task]}</b><p className="subtle">{tool.provider} / {tool.model}</p><small>{tool.is_enabled ? "Đã chọn · Chưa kết nối API" : "Đã tắt"}</small></div><div className="ai-tool-actions"><button disabled={busy} onClick={() => void toggle(tool)}>{tool.is_enabled ? "Tắt" : "Bật"}</button><button disabled={busy} onClick={() => { setEditing(tool); setView("configure"); }}>Sửa</button><button disabled={busy} onClick={() => setRemoving(tool)}>Xóa</button></div></div>)}</div></section> },
      { id: "configure", label: editing ? "Sửa công cụ" : "Thêm công cụ", content: <section className="card module-card"><h2>{editing ? "Sửa công cụ" : "Thêm công cụ"}</h2><form className="module-form" key={editing?.id ?? "new"} onSubmit={save}><div className="module-scroll"><label>Tác vụ<select name="task" defaultValue={editing?.task ?? "script"}>{Object.entries(tasks).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label><label>Provider<input name="provider" required maxLength={60} pattern="[a-zA-Z0-9._-]+" defaultValue={editing?.provider ?? ""} placeholder="Ví dụ: openai" /></label><label>Model<input name="model" required maxLength={100} pattern="[a-zA-Z0-9._:/-]+" defaultValue={editing?.model ?? ""} placeholder="Tên model do provider cung cấp" /></label><label className="ai-tool-check"><input type="checkbox" name="is_enabled" defaultChecked={editing?.is_enabled ?? true} /> Kích hoạt lựa chọn này</label></div><div className="module-actions"><button type="submit" disabled={busy}>{busy ? <Spinner label="Đang lưu…" /> : "Lưu công cụ"}</button>{editing && <button type="button" className="secondary-action" onClick={() => setEditing(null)}>Hủy sửa</button>}</div></form></section> },
    ]} />
    {removing && <ConfirmDialog title="Xóa công cụ AI?" description={`Xóa ${removing.provider} / ${removing.model} khỏi studio?`} onClose={() => setRemoving(null)} onConfirm={() => void remove()} busy={busy} />}
  </div>;
}
