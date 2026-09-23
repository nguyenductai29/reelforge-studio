"use client";
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import FlowEditor, { type Graph, type Workflow } from "./FlowEditor";
import AdminPanel from "./AdminPanel";
import BillingPanel from "./BillingPanel";
import AIToolsPanel from "./AIToolsPanel";
import Spinner from "../components/Spinner";
import Message from "../components/Message";
import { api } from "../lib/api";
import { errorMessage } from "../lib/messages";

type Project = { id: string; title: string; topic: string; status: string };
type Asset = { id: string; filename: string; bytes: number; content_type: string };
type Dashboard = { workspace: { id: string; name: string; plan: string }; is_admin: boolean; projects: Project[]; assets: Asset[]; workflows: Workflow[]; limits: { projects: number | null } };
type Settings = { workspace: { default_language: string; video_orientation: string; approval_required: boolean }; system: { frontend_origin: string; secure_cookies: boolean; storage_dir: string; trial_project_limit: number; registration_enabled: boolean } | null };
type Page = "dashboard" | "projects" | "library" | "workflows" | "ai" | "channels" | "calendar" | "analytics" | "settings" | "admin" | "billing";
const nav: { id: Page; label: string; icon: string }[] = [
  { id: "dashboard", label: "Tổng quan", icon: "◫" }, { id: "projects", label: "Dự án", icon: "▣" },
  { id: "library", label: "Kho media", icon: "▧" }, { id: "workflows", label: "Sơ đồ workflow", icon: "◇" },
  { id: "ai", label: "Công cụ AI", icon: "✧" }, { id: "channels", label: "Kênh đăng tải", icon: "↗" },
  { id: "calendar", label: "Lịch đăng", icon: "▦" }, { id: "analytics", label: "Phân tích", icon: "◷" },
  { id: "settings", label: "Cài đặt", icon: "⚙" },
  { id: "billing", label: "Gói & thanh toán", icon: "◈" },
];
function Card({ children, className = "" }: { children: ReactNode; className?: string }) { return <section className={`card ${className}`}>{children}</section>; }
function Empty({ children }: { children: ReactNode }) { return <div className="empty">{children}</div>; }
function Planned({title,description,items}:{title:string;description:string;items:string[]}){return <div className="roadmap-banner"><span className="tag">TRONG LỘ TRÌNH</span><h2>{title}</h2><p>{description}</p><div className="roadmap-items">{items.map(item=><span key={item}>{item} · Sắp có</span>)}</div></div>}

export default function Home() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [setup, setSetup] = useState(false);
  const [register, setRegister] = useState(false);
  const [canRegister, setCanRegister] = useState(false);
  const [page, setPage] = useState<Page>("dashboard");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState<string | null>(null);
  const [mediaSearch, setMediaSearch] = useState("");
  const [mediaType, setMediaType] = useState("all");
  const refresh = useCallback(async () => {
    try { setData(await api<Dashboard>("dashboard")); setError(""); }
    catch (e) {
      if (e instanceof Error && e.message === "Please sign in") {
        setData(null);
        const status = await api<{ setup_required: boolean; registration_enabled: boolean }>("status");
        setSetup(status.setup_required);
        setCanRegister(status.registration_enabled);
        if (status.setup_required || !status.registration_enabled) setRegister(false);
      } else setError(e instanceof Error ? e.message : String(e));
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => { if (new URLSearchParams(window.location.search).has("payment")) setPage("billing"); }, []);
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
    if (await submit(setup ? "setup" : register ? "register" : "login", { email: fields.get("email"), password: fields.get("password"), ...(register && !setup ? { workspace_name: fields.get("workspace_name") } : {}) })) form.reset();
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
      registration_enabled: fields.get("registration_enabled") === "on",
    };
    setBusy(true); setError("");
    try {
      await api(`settings/${scope}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setSettings(await api<Settings>("settings"));
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function saveGraph(workflowId: string, graph: Graph): Promise<boolean> {
    setError("");
    try {
      await api(`workflows/${encodeURIComponent(workflowId)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(graph) });
      await refresh();
      return true;
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); return false; }
  }
  if (loading) return <div className="loading"><Spinner label="Đang tải ReelForge Studio…" /></div>;
  if (!data) return <main className="auth-wrap"><Card className="auth-card"><div className="brand">✦ ReelForge <span>STUDIO</span></div><h1>{setup ? "Tạo studio của bạn" : register ? "Tạo tài khoản" : "Chào mừng trở lại"}</h1><p className="subtle">{setup ? "Tạo tài khoản quản trị đầu tiên để bắt đầu." : register ? "Tạo studio riêng với gói Trial để bắt đầu." : "Đăng nhập để quản lý các dự án video."}</p><form onSubmit={auth}>{register && !setup && <label>Tên studio<input name="workspace_name" maxLength={100} required placeholder="Ví dụ: Studio của tôi" /></label>}<label>Email<input name="email" type="email" autoComplete="username" required /></label><label>Mật khẩu<input name="password" type="password" autoComplete={setup || register ? "new-password" : "current-password"} minLength={setup || register ? 12 : undefined} required /></label><button disabled={busy}>{busy ? <Spinner label="Đang xử lý…" /> : setup ? "Tạo studio" : register ? "Tạo tài khoản Trial" : "Đăng nhập"}<span>→</span></button></form>{error && <Message>{errorMessage(error)}</Message>}{!setup && canRegister && <button className="auth-switch" type="button" disabled={busy} onClick={() => { setRegister(!register); setError(""); }}>{register ? "Đã có tài khoản? Đăng nhập" : "Chưa có tài khoản? Tạo tài khoản"}</button>}<p className="footnote">{setup || register ? "Mật khẩu tối thiểu 12 ký tự." : "Dữ liệu được lưu trên server của bạn."}</p></Card></main>;
  const { projects, assets, workflows, workspace, limits } = data;
  const activeWorkflow = workflows.find(w => w.id === selectedWorkflow) ?? workflows[0];
  const shownAssets = assets.filter(a => a.filename.toLowerCase().includes(mediaSearch.toLowerCase()) && (mediaType === "all" || a.content_type.startsWith(mediaType + "/")));
  return <div className="shell"><aside className="sidebar"><div className="brand">✦ ReelForge <span>STUDIO</span></div><div className="nav-label">WORKSPACE</div><nav>{[...nav, ...(data.is_admin ? [{ id: "admin" as Page, label: "Quản trị", icon: "♛" }] : [])].map(item => <button key={item.id} className={page === item.id ? "selected" : ""} onClick={() => { setPage(item.id); setError(""); }}><span className="nav-icon">{item.icon}</span>{item.label}</button>)}</nav><div className="sidebar-bottom"><div className="workspace-icon">RS</div><div className="workspace-meta"><b>{workspace.name}</b><small>{workspace.plan.toUpperCase()}</small></div><button className="logout" title="Đăng xuất" onClick={async () => { await api("logout", { method: "POST" }); await refresh(); }}>↪</button></div></aside>
    <main className="main"><header className="topbar"><div className="breadcrumb">Studio <span>/</span> {page === "admin" ? "Quản trị" : nav.find(n => n.id === page)?.label}</div><div className="top-right"><span className="online-dot" /> Hệ thống hoạt động <span className="plan-badge">{workspace.plan.toUpperCase()}</span></div></header><div className="content"><div className="heading"><div><p className="eyebrow">REELFORGE STUDIO</p><h1>{page === "admin" ? "Quản trị" : nav.find(n => n.id === page)?.label}</h1><p className="subtle">{page === "dashboard" ? "Tổng quan hoạt động trong studio của bạn." : page === "projects" ? "Tổ chức ý tưởng và các series video." : page === "library" ? "Tài nguyên cho mọi dự án của bạn." : page === "workflows" ? "Thiết kế node, nối các bước và lưu sơ đồ." : page === "ai" ? "Provider và model cho từng bước sản xuất." : page === "settings" ? "Thiết lập và trạng thái tài khoản." : "Không gian chuẩn bị cho giai đoạn tiếp theo."}</p></div><div className="date">{new Intl.DateTimeFormat("vi-VN", { dateStyle: "long" }).format(new Date())}</div></div>
    {error && <Message>{errorMessage(error)}</Message>}
    {page === "admin" && data.is_admin && <AdminPanel />}
    {page === "billing" && <BillingPanel onPayment={refresh} />}
    {page === "dashboard" && <>
      <div className="hero"><div className="hero-copy"><span className="hero-kicker">✦ CREATIVE CONTROL CENTER</span><h2>Biến ý tưởng thành<br/><em>câu chuyện có hình.</em></h2><p>Quản lý dự án, xây sơ đồ sản xuất và tái sử dụng media trong một studio.</p><div className="hero-actions"><button className="primary-action" onClick={() => setPage("projects")}>＋ Tạo dự án</button><button className="secondary-action" onClick={() => setPage("workflows")}>Xem sơ đồ workflow ↗</button></div></div><div className="hero-art" aria-hidden="true"><div className="orbit orbit-one"/><div className="orbit orbit-two"/><div className="hero-core">✦</div><span className="hero-chip chip-one">IDEA</span><span className="hero-chip chip-two">MEDIA</span><span className="hero-chip chip-three">RENDER</span></div></div>
      <div className="section-heading"><h2>Studio trong một góc nhìn</h2><span>DỮ LIỆU THẬT CỦA WORKSPACE</span></div>
      <div className="stat-grid"><Card><span className="stat-icon violet">▣</span><p>Dự án</p><strong>{projects.length}</strong><small>Đang quản lý</small></Card><Card><span className="stat-icon blue">▧</span><p>Kho media</p><strong>{assets.length}</strong><small>Tệp đã tải lên</small></Card><Card><span className="stat-icon green">◇</span><p>Workflow</p><strong>{workflows.length}</strong><small>Sơ đồ đã lưu</small></Card><Card><span className="stat-icon orange">◈</span><p>Gói hiện tại</p><strong className="plan-stat">{workspace.plan}</strong><small>{limits.projects === null ? "Không giới hạn dự án" : `${projects.length}/${limits.projects} dự án Trial`}</small></Card></div>
      <div className="two-col"><Card><div className="card-head"><h2>Dự án gần đây</h2><button className="text-button" onClick={() => setPage("projects")}>Xem tất cả →</button></div>{projects.length ? projects.slice(0,5).map(p => <div className="list-row" key={p.id}><div className="row-icon">▣</div><div><b>{p.title}</b><small>{p.topic || "Chưa có chủ đề"}</small></div><span className="status">{p.status}</span></div>) : <Empty>Chưa có dự án. Bắt đầu bằng ý tưởng đầu tiên của bạn.</Empty>}</Card><Card><div className="card-head"><h2>Quy trình của bạn</h2><button className="text-button" onClick={() => setPage("workflows")}>Mở sơ đồ →</button></div><div className="timeline">{["Ý tưởng & kịch bản", "Cảnh & media", "Giọng đọc & render", "Duyệt & xuất bản"].map((s,i) => <div key={s}><span>{String(i+1).padStart(2,"0")}</span>{s}</div>)}</div><p className="hint">Sơ đồ workflow đã chỉnh sửa và lưu được; engine tạo video tự động chưa triển khai.</p></Card></div>
    </>}
    {page === "projects" && <div className="two-col"><Card><h2>Tạo dự án</h2><p className="subtle">Đặt tên cho series hoặc video sắp làm.</p><form onSubmit={e => formSubmit(e,"projects")}><label>Tên dự án<input name="title" maxLength={150} required placeholder="Ví dụ: Truyện kinh dị Nhật Bản"/></label><label>Chủ đề<textarea name="topic" maxLength={3000} placeholder="Mô tả ý tưởng hoặc định hướng nội dung"/></label><button disabled={busy}>＋ Tạo dự án</button></form></Card><Card><div className="card-head"><h2>Danh sách dự án</h2><span className="tag">{projects.length}{limits.projects !== null ? ` / ${limits.projects} TRIAL` : ""}</span></div>{projects.length ? projects.map(p => <div className="list-row" key={p.id}><div className="row-icon">▣</div><div><b>{p.title}</b><small>{p.topic || "Chưa có chủ đề"}</small></div><span className="status">{p.status}</span></div>) : <Empty>Chưa có dự án nào.</Empty>}</Card></div>}
    {page === "library" && <><div className="library-top"><Card><h2>Tải media vào studio</h2><p className="subtle">Ảnh, video hoặc âm thanh · tối đa 100 MB mỗi tệp.</p><form onSubmit={e => formSubmit(e,"assets")}><label>Chọn tệp<input name="file" type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/webm,audio/mpeg,audio/wav,audio/ogg" required/></label><button disabled={busy}>↑ Tải lên kho</button></form></Card><Card><h2>Kho tái sử dụng</h2><p className="subtle">Lưu và tải xuống media theo workspace. Tính năng gắn tag, xem trước và ghép cảnh nằm trong lộ trình.</p><div className="library-number">{assets.length}<span>tệp đang lưu</span></div></Card></div><div className="library-list card"><div className="card-head"><h2>Thư viện</h2><span className="tag">{shownAssets.length} KẾT QUẢ</span></div><div className="library-filters"><input aria-label="Tìm tên tệp" value={mediaSearch} onChange={e => setMediaSearch(e.target.value)} placeholder="Tìm theo tên tệp…"/><select aria-label="Lọc loại media" value={mediaType} onChange={e => setMediaType(e.target.value)}><option value="all">Tất cả</option><option value="image">Ảnh</option><option value="video">Video</option><option value="audio">Âm thanh</option></select></div>{shownAssets.length ? shownAssets.map(a => <div className="list-row" key={a.id}><div className="row-icon">{a.content_type.startsWith("video/")?"▶":a.content_type.startsWith("audio/")?"♫":"▧"}</div><div><a href={`/api/assets/${encodeURIComponent(a.id)}`}>{a.filename}</a><small>{a.content_type} · {(a.bytes/1048576).toFixed(2)} MB</small></div><a className="asset-link" href={`/api/assets/${encodeURIComponent(a.id)}`}>Tải xuống ↗</a></div>) : <Empty>{assets.length ? "Không tìm thấy tệp phù hợp." : "Kho media còn trống."}</Empty>}</div></>}
    {page === "workflows" && <><div className="workflow-toolbar"><div className="workflow-picker"><label htmlFor="workflow-select">SƠ ĐỒ ĐANG XEM</label><select id="workflow-select" value={activeWorkflow?.id ?? ""} onChange={e => setSelectedWorkflow(e.target.value)} disabled={!workflows.length}>{workflows.length ? workflows.map(w => <option key={w.id} value={w.id}>{w.name}</option>) : <option value="">Chưa có workflow</option>}</select></div><form className="inline-create" onSubmit={async e => {e.preventDefault();const form=e.currentTarget;const name=String(new FormData(form).get("name") ?? "");setBusy(true);setError("");try{const result=await api<Workflow>("workflows",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name})});setSelectedWorkflow(result.id);await refresh();form.reset()}catch(err){setError(err instanceof Error?err.message:String(err))}finally{setBusy(false)}}}><label>TẠO WORKFLOW MỚI<input name="name" maxLength={150} required placeholder="Tên workflow…"/></label><button disabled={busy}>＋ Tạo</button></form></div>{activeWorkflow ? <FlowEditor key={activeWorkflow.id} workflow={activeWorkflow} onSave={graph => saveGraph(activeWorkflow.id,graph)}/> : <Card><Empty>Tạo workflow để mở sơ đồ node đầu tiên.</Empty></Card>}</>}
    {page === "ai" && <AIToolsPanel />}
    {page === "channels" && <Planned title="Kênh đăng tải" description="Kết nối YouTube, Facebook và TikTok để đăng video sau khi được duyệt. OAuth và publisher chưa được triển khai." items={["YouTube", "Facebook", "TikTok"]}/>}
    {page === "calendar" && <Planned title="Lịch đăng" description="Chọn thời điểm và trạng thái từng bài đăng sau khi publisher hoạt động. Hiện chưa có lịch hoặc job đăng tải." items={["Lịch theo tháng", "Hàng đợi đăng", "Lịch sử đăng"]}/>}
    {page === "analytics" && <Planned title="Phân tích" description="Theo dõi video đã đăng, hiệu suất từng kênh, mức sử dụng AI và chi phí. Chưa có dữ liệu hoặc đồng bộ analytics." items={["Hiệu suất video", "Sử dụng AI", "Chi phí và credits"]}/>}
    {page === "settings" && <div className="two-col"><Card><h2>Workspace: {workspace.name}</h2><p className="subtle">Gói {workspace.plan.toUpperCase()} · ID {workspace.id}</p>{settings && <form key={JSON.stringify(settings.workspace)} onSubmit={e => saveSettings(e, "workspace")}><label>Ngôn ngữ mặc định<select name="default_language" defaultValue={settings.workspace.default_language}><option value="vi">Tiếng Việt</option><option value="en">English</option><option value="ja">日本語</option></select></label><label>Tỷ lệ video<select name="video_orientation" defaultValue={settings.workspace.video_orientation}><option value="vertical">Dọc (9:16)</option><option value="horizontal">Ngang (16:9)</option><option value="square">Vuông (1:1)</option></select></label><label className="check-label"><input name="approval_required" type="checkbox" defaultChecked={settings.workspace.approval_required} /> Yêu cầu duyệt trước khi đăng</label><button disabled={busy}>Lưu cài đặt workspace</button></form>}</Card><Card><h2>Cài đặt hệ thống</h2>{settings?.system && data.is_admin ? <form key={JSON.stringify(settings.system)} onSubmit={e => saveSettings(e, "system")}><label>Địa chỉ frontend<input name="frontend_origin" type="url" required defaultValue={settings.system.frontend_origin} /></label><label>Giới hạn dự án Trial<input name="trial_project_limit" type="number" min={1} max={10000} required defaultValue={settings.system.trial_project_limit} /></label><label className="check-label"><input name="secure_cookies" type="checkbox" defaultChecked={settings.system.secure_cookies} /> Cookie chỉ qua HTTPS</label><label className="check-label"><input name="registration_enabled" type="checkbox" defaultChecked={settings.system.registration_enabled} /> Cho phép đăng ký tài khoản Trial</label><button disabled={busy}>Lưu cài đặt hệ thống</button><p className="hint">Thư mục media: {settings.system.storage_dir}. Chuyển dữ liệu media cần thực hiện trên server.</p></form> : <p className="subtle">Chỉ quản trị viên được xem và chỉnh sửa cài đặt hệ thống.</p>}<p className="hint">Kết nối AI, mạng xã hội và thanh toán sẽ có khi các module tương ứng được triển khai.</p></Card></div>}
    </div></main></div>;
}
