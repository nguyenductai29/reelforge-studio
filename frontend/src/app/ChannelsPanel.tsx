"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { errorMessage } from "../lib/messages";
import Message from "../components/Message";
import Spinner from "../components/Spinner";
import ConfirmDialog from "../components/ConfirmDialog";

type Connection = { connected: boolean; expires_at: string | null; scope: string | null };

export default function ChannelsPanel() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const load = useCallback(async () => {
    try { setConnection(await api<Connection>("youtube/connection")); setError(""); }
    catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function connect() {
    setBusy(true); setError("");
    try {
      const start = await api<{ url: string }>("youtube/authorization");
      window.location.assign(start.url);
    } catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); setBusy(false); }
  }
  async function disconnect() {
    setBusy(true); setError("");
    try { await api("youtube/connection", { method: "DELETE" }); setConfirmDisconnect(false); await load(); }
    catch (e) { setError(errorMessage(e instanceof Error ? e.message : String(e))); }
    finally { setBusy(false); }
  }
  return <section className="card module-card"><div className="card-head"><div><h2>Kênh YouTube</h2><p className="subtle">Kết nối tài khoản Google để cấp quyền tải video đã duyệt lên YouTube.</p></div><span className="tag">{connection?.connected ? "ĐÃ KẾT NỐI" : "CHƯA KẾT NỐI"}</span></div>
    {error && <Message>{error}</Message>}
    {!connection ? <Spinner label="Đang kiểm tra kết nối…" /> : <div className="module-scroll"><p>{connection.connected ? "ReelForge có quyền YouTube upload cho workspace này." : "Chỉ chủ workspace có thể bắt đầu kết nối OAuth."}</p>{connection.connected && connection.expires_at && <p className="module-help">Access token hiện tại hết hạn lúc {new Date(connection.expires_at).toLocaleString("vi-VN")}; server sẽ dùng refresh token khi cần.</p>}<p className="module-help">Facebook Page Reels và TikTok cần quyền ứng dụng và kết nối kênh riêng trước khi có thể đăng từ ReelForge.</p></div>}
    <div className="module-actions">{connection?.connected ? <button className="secondary-action" disabled={busy} onClick={() => setConfirmDisconnect(true)}>Ngắt kết nối</button> : <button disabled={busy || !connection} onClick={() => void connect()}>{busy ? "Đang chuyển đến Google…" : "Kết nối YouTube"}</button>}</div>
    {confirmDisconnect && <ConfirmDialog title="Ngắt kết nối YouTube?" description="ReelForge sẽ xóa token của workspace này. Video đã tải lên YouTube không bị xóa." onClose={() => setConfirmDisconnect(false)} onConfirm={() => void disconnect()} busy={busy} />}
  </section>;
}
