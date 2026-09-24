"use client";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, jsonRequest } from "../lib/api";
import { errorMessage } from "../lib/messages";
import Message from "../components/Message";

type Connection = { connected: boolean };
type Publication = { id: string; state: string; remote_id: string | null; last_error: string | null; can_retry: boolean };
const states: Record<string, string> = { queued: "Trong hàng đợi", uploading: "Đang tải lên", succeeded: "Đã tải riêng tư", failed: "Tải lên thất bại", needs_attention: "Cần kiểm tra trên YouTube" };
const errors: Record<string, string> = { connection_changed: "Kết nối YouTube đã thay đổi. Kiểm tra kênh trước khi thử lại.", asset_unavailable: "Không tìm thấy video đã duyệt.", submission_without_saved_session: "Phiên tải lên có kết quả không rõ. Kiểm tra YouTube Studio trước khi tiếp tục." };

export default function YouTubePublish({ runId, assetId, defaultTitle }: { runId: string; assetId: string; defaultTitle: string }) {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [publication, setPublication] = useState<Publication | null>(null);
  const [title, setTitle] = useState(defaultTitle.slice(0, 100));
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const [connection, rows] = await Promise.all([
        api<Connection>("youtube/connection"),
        api<Publication[]>(`youtube/publications?run_id=${encodeURIComponent(runId)}`),
      ]);
      setConnected(connection.connected);
      setPublication(rows[0] ?? null);
      setError("");
    } catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
  }, [runId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!publication || !["queued", "uploading"].includes(publication.state)) return;
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [publication?.id, publication?.state, load]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      setPublication(await api<Publication>("youtube/publications", jsonRequest("POST", {
        run_id: runId, asset_id: assetId, title: title.trim(), description,
      })));
    } catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
    finally { setBusy(false); }
  }
  async function retry() {
    if (!publication) return;
    setBusy(true); setError("");
    try { setPublication(await api<Publication>(`youtube/publications/${encodeURIComponent(publication.id)}/retry`, { method: "POST" })); }
    catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
    finally { setBusy(false); }
  }
  return <section className="run-detail"><h3>YouTube</h3><p className="subtle">Tải bản đã duyệt lên YouTube ở chế độ riêng tư. Bạn kiểm tra và đổi quyền hiển thị trong YouTube Studio.</p>
    {error && <Message>{error}</Message>}
    {publication ? <><p>{states[publication.state] ?? publication.state}</p>{publication.last_error && <p className="module-help">{errors[publication.last_error] ?? publication.last_error}</p>}{publication.remote_id && <a href={`https://studio.youtube.com/video/${encodeURIComponent(publication.remote_id)}/edit`} target="_blank" rel="noopener noreferrer">Mở video trong YouTube Studio ↗</a>}{publication.can_retry && (connected ? <p><button type="button" disabled={busy} onClick={() => void retry()}>{busy ? "Đang xếp hàng…" : "Thử lại tải lên YouTube"}</button></p> : <p>Kết nối lại YouTube trước khi thử lại. <a href="/?channel=youtube">Mở trang kết nối kênh</a>.</p>)}</> : connected === false ? <p>Chưa kết nối YouTube. <a href="/?channel=youtube">Mở trang kết nối kênh</a>.</p> : connected === null ? <p>Đang kiểm tra kênh…</p> : <form className="module-form" onSubmit={submit}><label>Tiêu đề YouTube<input value={title} onChange={e => setTitle(e.target.value)} maxLength={100} required /></label><label>Mô tả<textarea value={description} onChange={e => setDescription(e.target.value)} maxLength={5000} /></label><button disabled={busy || !title.trim()}>{busy ? "Đang xếp hàng…" : "Tải riêng tư lên YouTube"}</button></form>}
  </section>;
}
