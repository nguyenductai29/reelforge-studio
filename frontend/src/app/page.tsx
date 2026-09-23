"use client";
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";

type Project = { id: string; title: string; topic: string; status: string };
type Asset = { id: string; filename: string; bytes: number; content_type: string };
type Workflow = { id: string; name: string; steps: string[] };
type Dashboard = { workspace: { id: string; name: string; plan: string }; is_admin: boolean; projects: Project[]; assets: Asset[]; workflows: Workflow[]; limits: { projects: number | null } };
type Settings = { workspace: { default_language: string; video_orientation: string; approval_required: boolean }; system: { frontend_origin: string; secure_cookies: boolean; storage_dir: string; trial_project_limit: number } | null };
type Page = "dashboard" | "projects" | "library" | "workflows" | "settings";
const nav: { id: Page; label: string; icon: string }[] = [
  { id: "dashboard", label: "Tổng quan", icon: "◫" }, { id: "projects", label: "Dự án", icon: "▣" },
  { id: "library", label: "Kho media", icon: "▧" }, { id: "workflows", label: "Workflow", icon: "◇" },
  { id: "settings", label: "Cài đặt", icon: "⚙" },
];
async function api<T>(endpoint: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/${endpoint}`, { credentials: "same-origin", cache: "no-store", ...init });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? `Yêu cầu thất bại (${res.status})`);
  return data as T;
}
function Card({ children, className = "" }: { children: ReactNode; className?: string }) { return <section className={`card ${className}`}>{children}</section>; }
function Empty({ children }: { children: ReactNode }) { return <div className="empty">{children}</div>; }

export default function Home() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [setup, setSetup] = useState(false);
  const [page, setPage] = useState<Page>("dashboard");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  const refresh = useCallback(async () => {
    try { setData(await api<Dashboard>("dashboard")); setError(""); }
    catch (e) {
      if (e instanceof Error && e.message === "Please sign in") {
        setData(null); setSetup((await api<{ setup_required: boolean }>("status")).setup_required);
      } else setError(e instanceof Error ? e.message : String(e));
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (page === "settings" && data) {
      void api<Settings>("settings").then(setSettings).catch(e => setError(e instanceof Error ? e.message : String(e)));
    }
  }, [page, data]);
  async function submit(endpoint: string, body: FormData | object): Promise<boolean> {
    setBusy(true); setError("");
    try {
      await api(endpoint, { method: "POST", ...(body instanceof FormData ? { body } : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }) });
      await refresh();
      return true;
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); return false; }
    finally { setBusy(false); }
  }
  async function auth(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); const form = e.currentTarget; const fields = new FormData(form);
    if (await submit(setup ? "setup" : "login", { email: fields.get("email"), password: fields.get("password") })) form.reset();
  }
  async function formSubmit(e: FormEvent<HTMLFormElement>, endpoint: string) {
    e.preventDefault(); const form = e.currentTarget; const fields = new FormData(form);
    if (await submit(endpoint, endpoint === "assets" ? fields : Object.fromEntries(fields))) form.reset();
  }
  async function saveSettings(e: FormEvent<HTMLFormElement>, scope: "workspace" | "system") {
    e.preventDefault();
    const fields = new FormData(e.currentTarget);
    const body = scope === "workspace" ? {
      default_language: fields.get("default_language"),
      video_orientation: fields.get("video_orientation"),
      approval_required: fields.get("approval_required") === "on",
    } : {
      frontend_origin: fields.get("frontend_origin"),
      secure_cookies: fields.get("secure_cookies") === "on",
      trial_project_limit: Number(fields.get("trial_project_limit")),
    };
    setBusy(true); setError("");
    try {
      await api(`settings/${scope}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setSettings(await api<Settings>("settings"));
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  if (loading) return <div className="loading">Đang tải ReelForge Studio…</div>;
  if (!data) return <main className="auth-wrap"><Card className="auth-card"><div className="brand">✦ ReelForge <span>STUDIO</span></div><h1>{setup ? "Tạo studio của bạn" : "Chào mừng trở lại"}</h1><p className="subtle">{setup ? "Tạo tài khoản quản trị đầu tiên để bắt đầu." : "Đăng nhập để quản lý các dự án video."}</p><form onSubmit={auth}><label>Email<input name="email" type="email" autoComplete="username" required /></label><label>Mật khẩu<input name="password" type="password" autoComplete={setup ? "new-password" : "current-password"} minLength={setup ? 12 : undefined} required /></label><button disabled={busy}>{setup ? "Tạo studio" : "Đăng nhập"} <span>→</span></button></form>{error && <p className="error" role="alert">{error}</p>}<p className="footnote">{setup ? "Mật khẩu tối thiểu 12 ký tự." : "Dữ liệu được lưu trên server của bạn."}</p></Card></main>;
  const { projects, assets, workflows, workspace, limits } = data;
  return <div className="shell"><aside className="sidebar"><div className="brand">✦ ReelForge <span>STUDIO</span></div><div className="nav-label">WORKSPACE</div><nav>{nav.map(item => <button key={item.id} className={page === item.id ? "selected" : ""} onClick={() => { setPage(item.id); setError(""); }}><span className="nav-icon">{item.icon}</span>{item.label}</button>)}</nav><div className="sidebar-bottom"><div className="workspace-icon">RS</div><div className="workspace-meta"><b>{workspace.name}</b><small>{workspace.plan.toUpperCase()}</small></div><button className="logout" title="Đăng xuất" onClick={async () => { await api("logout", { method: "POST" }); await refresh(); }}>↪</button></div></aside>
    <main className="main"><header className="topbar"><div className="breadcrumb">Studio <span>/</span> {nav.find(n => n.id === page)?.label}</div><div className="top-right"><span className="online-dot" /> Hệ thống hoạt động <span className="plan-badge">{workspace.plan.toUpperCase()}</span></div></header><div className="content"><div className="heading"><div><p className="eyebrow">REELFORGE STUDIO</p><h1>{nav.find(n => n.id === page)?.label}</h1><p className="subtle">{page === "dashboard" ? "Tổng quan hoạt động trong studio của bạn." : page === "projects" ? "Tổ chức ý tưởng và các series video." : page === "library" ? "Tài nguyên cho mọi dự án của bạn." : page === "workflows" ? "Quản lý các bước trong quy trình sáng tạo." : "Thiết lập và trạng thái tài khoản."}</p></div><div className="date">{new Intl.DateTimeFormat("vi-VN", { dateStyle: "long" }).format(new Date())}</div></div>
    {error && <div className="error" role="alert">{error}</div>}
    {page === "dashboard" && <><div className="stat-grid"><Card><span className="stat-icon violet">▣</span><p>Dự án</p><strong>{projects.length}</strong><small>Đang quản lý</small></Card><Card><span className="stat-icon blue">▧</span><p>Media</p><strong>{assets.length}</strong><small>Tài nguyên đã tải lên</small></Card><Card><span className="stat-icon green">◇</span><p>Workflow</p><strong>{workflows.length}</strong><small>Quy trình mẫu</small></Card><Card><span className="stat-icon orange">◈</span><p>Gói hiện tại</p><strong className="plan-stat">{workspace.plan}</strong><small>{limits.projects === null ? "Không giới hạn dự án" : `${projects.length}/${limits.projects} dự án`}</small></Card></div><div className="two-col"><Card><div className="card-head"><h2>Dự án gần đây</h2><button className="text-button" onClick={() => setPage("projects")}>Xem tất cả →</button></div>{projects.length ? projects.slice(0, 5).map(p => <div className="list-row" key={p.id}><div className="row-icon">▣</div><div><b>{p.title}</b><small>{p.topic || "Chưa có chủ đề"}</small></div><span className="status">{p.status}</span></div>) : <Empty>Chưa có dự án. Tạo một dự án để bắt đầu.</Empty>}</Card><Card><div className="card-head"><h2>Quy trình sản xuất</h2><span className="tag">KẾ HOẠCH</span></div><div className="timeline">{["Ý tưởng & kịch bản", "Cảnh & media", "Giọng đọc & render", "Duyệt & xuất bản"].map((s, i) => <div key={s}><span>{String(i + 1).padStart(2, "0")}</span>{s}</div>)}</div><p className="hint">Tạo video, render và đăng tải tự động sẽ được triển khai trong giai đoạn tiếp theo.</p></Card></div></>}
    {page === "projects" && <div className="two-col"><Card><h2>Tạo dự án</h2><p className="subtle">Đặt tên cho series hoặc video sắp làm.</p><form onSubmit={e => formSubmit(e, "projects")}><label>Tên dự án<input name="title" maxLength={150} required placeholder="Ví dụ: Truyện kinh dị Nhật Bản" /></label><label>Chủ đề<textarea name="topic" maxLength={3000} placeholder="Mô tả ý tưởng hoặc định hướng nội dung" /></label><button disabled={busy}>＋ Tạo dự án</button></form></Card><Card><div className="card-head"><h2>Danh sách dự án</h2><span className="tag">{projects.length}{limits.projects !== null ? ` / ${limits.projects} TRIAL` : ""}</span></div>{projects.length ? projects.map(p => <div className="list-row" key={p.id}><div className="row-icon">▣</div><div><b>{p.title}</b><small>{p.topic || "Chưa có chủ đề"}</small></div><span className="status">{p.status}</span></div>) : <Empty>Chưa có dự án nào.</Empty>}</Card></div>}
    {page === "library" && <div className="two-col"><Card><h2>Tải media</h2><p className="subtle">Ảnh, video và âm thanh tối đa 100 MB mỗi tệp.</p><form onSubmit={e => formSubmit(e, "assets")}><label>Chọn tệp<input name="file" type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/webm,audio/mpeg,audio/wav,audio/ogg" required /></label><button disabled={busy}>↑ Tải lên kho</button></form></Card><Card><div className="card-head"><h2>Tài nguyên</h2><span className="tag">{assets.length} TỆP</span></div>{assets.length ? assets.map(a => <div className="list-row" key={a.id}><div className="row-icon">▧</div><div><a href={`/api/assets/${encodeURIComponent(a.id)}`}>{a.filename}</a><small>{a.content_type} · {(a.bytes / 1048576).toFixed(2)} MB</small></div></div>) : <Empty>Kho media còn trống.</Empty>}</Card></div>}
    {page === "workflows" && <div className="two-col"><Card><h2>Tạo workflow mẫu</h2><p className="subtle">Đặt tên cho luồng sản xuất video của bạn.</p><form onSubmit={e => formSubmit(e, "workflows")}><label>Tên workflow<input name="name" maxLength={150} required placeholder="Ví dụ: Short phim kinh dị" /></label><button disabled={busy}>＋ Tạo workflow</button></form><p className="hint">Workflow hiện là mẫu lưu trong database, chưa chạy tự động.</p></Card><Card><div className="card-head"><h2>Workflow đã lưu</h2><span className="tag">{workflows.length} MẪU</span></div>{workflows.length ? workflows.map(w => <div className="workflow-row" key={w.id}><b>◇ {w.name}</b><p>{w.steps.join(" → ")}</p></div>) : <Empty>Chưa có workflow nào.</Empty>}</Card></div>}
    {page === "settings" && <div className="two-col"><Card><h2>Workspace: {workspace.name}</h2><p className="subtle">Gói {workspace.plan.toUpperCase()} · ID {workspace.id}</p>{settings && <form key={JSON.stringify(settings.workspace)} onSubmit={e => saveSettings(e, "workspace")}><label>Ngôn ngữ mặc định<select name="default_language" defaultValue={settings.workspace.default_language}><option value="vi">Tiếng Việt</option><option value="en">English</option><option value="ja">日本語</option></select></label><label>Tỷ lệ video<select name="video_orientation" defaultValue={settings.workspace.video_orientation}><option value="vertical">Dọc (9:16)</option><option value="horizontal">Ngang (16:9)</option><option value="square">Vuông (1:1)</option></select></label><label className="check-label"><input name="approval_required" type="checkbox" defaultChecked={settings.workspace.approval_required} /> Yêu cầu duyệt trước khi đăng</label><button disabled={busy}>Lưu cài đặt workspace</button></form>}</Card><Card><h2>Cài đặt hệ thống</h2>{settings?.system && data.is_admin ? <form key={JSON.stringify(settings.system)} onSubmit={e => saveSettings(e, "system")}><label>Địa chỉ frontend<input name="frontend_origin" type="url" required defaultValue={settings.system.frontend_origin} /></label><label>Giới hạn dự án Trial<input name="trial_project_limit" type="number" min={1} max={10000} required defaultValue={settings.system.trial_project_limit} /></label><label className="check-label"><input name="secure_cookies" type="checkbox" defaultChecked={settings.system.secure_cookies} /> Cookie chỉ qua HTTPS</label><button disabled={busy}>Lưu cài đặt hệ thống</button><p className="hint">Thư mục media: {settings.system.storage_dir}. Chuyển dữ liệu media cần thực hiện trên server.</p></form> : <p className="subtle">Chỉ quản trị viên được xem và chỉnh sửa cài đặt hệ thống.</p>}<p className="hint">Kết nối AI, mạng xã hội và thanh toán sẽ có khi các module tương ứng được triển khai.</p></Card></div>}
    </div></main></div>;
}
