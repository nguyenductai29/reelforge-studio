"use client";
import { useEffect, useRef, useState } from "react";
import { api, jsonRequest } from "../../../lib/api";

export default function YouTubeCallback() {
  const [message, setMessage] = useState("Đang hoàn tất kết nối YouTube…");
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const query = new URLSearchParams(window.location.search);
    const state = query.get("state");
    const code = query.get("code");
    window.history.replaceState(null, "", window.location.pathname);
    if (query.get("error")) { setMessage("Kết nối YouTube đã bị hủy hoặc Google từ chối quyền."); return; }
    if (!state || !code) { setMessage("Thiếu mã xác thực từ Google."); return; }
    void api("youtube/callback", jsonRequest("POST", { state, code }))
      .then(() => window.location.replace("/?channel=youtube"))
      .catch(error => setMessage(error instanceof Error ? error.message : "Không thể kết nối YouTube."));
  }, []);
  return <main className="auth-wrap"><section className="card auth-card"><h1>Kết nối YouTube</h1><p>{message}</p><a href="/?channel=youtube">Quay về ReelForge Studio</a></section></main>;
}
