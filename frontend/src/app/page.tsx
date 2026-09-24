"use client";
import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import ControlRoom from "./ControlRoom";
import ModuleTabs from "../components/ModuleTabs";
import StudioIcon from "../components/StudioIcon";
import FlowEditor, { type Graph, type Workflow } from "./FlowEditor";
import AdminPanel from "./AdminPanel";
import BillingPanel from "./BillingPanel";
import AIToolsPanel from "./AIToolsPanel";
import WorkflowRuns from "./WorkflowRuns";
import ChannelsPanel from "./ChannelsPanel";
import Spinner from "../components/Spinner";
import Message from "../components/Message";
import { api } from "../lib/api";
import { errorMessage } from "../lib/messages";

type Project = { id: string; title: string; topic: string; status: string };
type Asset = { id: string; filename: string; bytes: number; content_type: string; project_id: string | null; run_id: string | null };
type Dashboard = { workspace: { id: string; name: string; plan: string }; is_admin: boolean; projects: Project[]; assets: Asset[]; workflows: Workflow[]; limits: { projects: number | null } };
type Settings = { workspace: { default_language: string; video_orientation: string; approval_required: boolean }; system: { frontend_origin: string; secure_cookies: boolean; storage_dir: string; trial_project_limit: number; registration_enabled: boolean } | null };
type Page = "dashboard" | "projects" | "library" | "workflows" | "ai" | "channels" | "calendar" | "analytics" | "settings" | "admin" | "billing";
const nav: { id: Page; label: string }[] = [
  { id: "dashboard", label: "Tổng quan" }, { id: "projects", label: "Dự án" },
  { id: "library", label: "Kho media" }, { id: "workflows", label: "Sơ đồ workflow" },
  { id: "ai", label: "Công cụ AI" }, { id: "channels", label: "Kênh đăng tải" },
  { id: "calendar", label: "Lịch đăng" }, { id: "analytics", label: "Phân tích" },
  { id: "settings", label: "Cài đặt" },
  { id: "billing", label: "Gói & thanh toán" },
];
function Card({ children, className = "" }: { children: ReactNode; className?: string }) { return <section className={`card ${className}`}>{children}</section>; }
function Empty({ children }: { children: ReactNode }) { return <div className="empty">{children}</div>; }
function Planned({title,description,items}:{title:string;description:string;items:string[]}){return <div className="roadmap-banner module-scroll" tabIndex={0}><span className="tag">TRONG LỘ TRÌNH</span><h2>{title}</h2><p>{description}</p><div className="roadmap-items">{items.map(item=><span key={item}>{item} · Sắp có</span>)}</div></div>}

export default function Home() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [setup, setSetup] = useState(false);
  const [register, setRegister] = useState(false);
  const [canRegister, setCanRegister] = useState(false);
  const [page, setPage] = useState<Page>("dashboard");
  const [navOpen, setNavOpen] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLButtonElement>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState<string | null>(null);
  const [workflowDirty, setWorkflowDirty] = useState(false);
  const [workflowView, setWorkflowView] = useState("graph");
  const [projectsView, setProjectsView] = useState("list");
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [libraryView, setLibraryView] = useState("media");
  const [runSteps, setRunSteps] = useState<Record<string,string>>({});
  const [mediaSearch, setMediaSearch] = useState("");
  const [mediaType, setMediaType] = useState("all");
  const inspectRun = useCallback((steps: {node_id:string;status:string}[] | null) => {
    setRunSteps(Object.fromEntries((steps ?? []).map(step => [step.node_id,step.status])));
  }, []);
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
  useEffect(() => {
    setNavOpen(false);
    contentRef.current?.scrollTo({ top: 0 });
    const heading = contentRef.current?.querySelector("h1");
    if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
  }, [page]);
  useEffect(() => {
    if (!navOpen) return;
    const closeMenu = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setNavOpen(false); menuRef.current?.focus(); }
    };
    window.addEventListener("keydown", closeMenu);
    return () => window.removeEventListener("keydown", closeMenu);
  }, [navOpen]);
  useEffect(() => { const query = new URLSearchParams(window.location.search); if (query.has("payment")) setPage("billing"); else if (query.get("channel") === "youtube") setPage("channels"); }, []);
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
  async function saveProject(e: FormEvent<HTMLFormElement>, projectId: string) {
    e.preventDefault();
    const fields = new FormData(e.currentTarget);
    setBusy(true); setError("");
    try {
      await api(`projects/${encodeURIComponent(projectId)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: fields.get("title"), topic: fields.get("topic") }) });
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
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
  const activeProject = projects.find(p => p.id === selectedProject);
  const activeWorkflow = workflows.find(w => w.id === selectedWorkflow) ?? workflows[0];
  const shownAssets = assets.filter(a => a.filename.toLowerCase().includes(mediaSearch.toLowerCase()) && (mediaType === "all" || a.content_type.startsWith(mediaType + "/")));
  return <div className="shell broadcast-shell">
    <aside className={`sidebar ${navOpen ? "is-nav-open" : ""}`}>
      <div className="studio-brand"><span className="studio-brand-mark" aria-hidden="true">r.</span><span className="studio-brand-name">ReelForge<small>STUDIO</small></span><button ref={menuRef} type="button" className="mobile-nav-toggle" aria-label={navOpen ? "Đóng menu điều hướng" : "Mở menu điều hướng"} aria-expanded={navOpen} aria-controls="studio-navigation" onClick={() => setNavOpen(open => !open)}><StudioIcon name={navOpen ? "close" : "menu"} /></button></div>
      <div className="nav-label">WORKSPACE</div>
      <nav id="studio-navigation" aria-label="Điều hướng studio">{[...nav, ...(data.is_admin ? [{ id: "admin" as Page, label: "Quản trị" }] : [])].map(item => <button type="button" key={item.id} className={page === item.id ? "selected" : ""} aria-current={page === item.id ? "page" : undefined} aria-label={item.label} title={item.label} onClick={() => { setPage(item.id); setNavOpen(false); setError(""); }}><span className="nav-icon"><StudioIcon name={item.id} size={18} /></span><span className="studio-nav-text">{item.label}</span></button>)}</nav>
      <div className="sidebar-bottom"><div className="workspace-icon">{workspace.name.slice(0, 2).toUpperCase()}</div><div className="workspace-meta"><b>{workspace.name}</b><small>{workspace.plan.toUpperCase()}</small></div><button type="button" className="logout" title="Đăng xuất" aria-label="Đăng xuất" onClick={async () => { try { await api("logout", { method: "POST" }); setNavOpen(false); await refresh(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } }}><StudioIcon name="logout" size={17} /></button></div>
    </aside>
    <main className="main"><header className="topbar"><div className="breadcrumb"><StudioIcon name="projects" size={14} /><span>Studio /</span>{page === "dashboard" ? "Control Room" : page === "admin" ? "Quản trị" : nav.find(n => n.id === page)?.label}</div><div className="top-right"><span className="online-dot" /><span className="connection-text">Đã kết nối studio</span><span className="plan-badge">{workspace.plan.toUpperCase()}</span></div></header>
    <div className={`content ${page === "dashboard" ? "control-room-content" : ""}`} data-module={page} ref={contentRef}>
      {page !== "dashboard" && <div className="heading"><div><p className="eyebrow">REELFORGE STUDIO</p><h1>{page === "admin" ? "Quản trị" : nav.find(n => n.id === page)?.label}</h1><p className="subtle">{page === "projects" ? "Tổ chức ý tưởng và các series video." : page === "library" ? "Tài nguyên cho mọi dự án của bạn." : page === "workflows" ? "Thiết kế sơ đồ, chạy thử và theo dõi trạng thái từng node." : page === "ai" ? "Provider và model cho từng bước sản xuất." : page === "settings" ? "Thiết lập và trạng thái tài khoản." : "Không gian chuẩn bị cho giai đoạn tiếp theo."}</p></div><div className="date">{new Intl.DateTimeFormat("vi-VN", { dateStyle: "long" }).format(new Date())}</div></div>}
    {error && <Message>{errorMessage(error)}</Message>}
    <div className="module-body">
    {page === "admin" && data.is_admin && <AdminPanel />}
    {page === "billing" && <BillingPanel onPayment={refresh} />}
    {page === "dashboard" && <ControlRoom assets={assets} projects={projects} workflows={workflows} projectLimit={limits.projects} onNavigate={(destination, view) => { if (destination === "projects") setProjectsView(view ?? "list"); if (destination === "library") setLibraryView(view ?? "media"); if (destination === "workflows") setWorkflowView(view ?? "graph"); setPage(destination); }} onOpenWorkflow={id => { setSelectedWorkflow(id); setWorkflowDirty(false); setRunSteps({}); setWorkflowView("graph"); setPage("workflows"); }} />}
    {page === "projects" && <ModuleTabs split activeTab={projectsView} onTabChange={setProjectsView} tabs={[
      { id: "list", label: `Danh sách dự án (${projects.length})`, content: <Card className="module-card"><div className="card-head"><h2>Danh sách dự án</h2><span className="tag">{projects.length}{limits.projects !== null ? ` / ${limits.projects} TRIAL` : ""}</span></div><div className="module-scroll" tabIndex={0} aria-label="Danh sách dự án">{projects.length ? projects.map(p => <div className="list-row" key={p.id}><div className="row-icon">▣</div><div><b>{p.title}</b><small>{p.topic || "Chưa có chủ đề"}</small></div><button className="secondary-action" type="button" onClick={() => { setSelectedProject(p.id); setProjectsView("detail"); }}>Mở dự án</button></div>) : <Empty>Chưa có dự án nào.</Empty>}</div></Card> },
      { id: "create", label: "Tạo dự án", content: <Card className="module-card"><h2>Tạo dự án</h2><form className="module-form" onSubmit={e => formSubmit(e,"projects")}><div className="module-scroll"><p className="module-help">Đặt tên cho series hoặc video sắp làm.</p><label>Tên dự án<input name="title" maxLength={150} required placeholder="Ví dụ: Truyện kinh dị Nhật Bản"/></label><label>Chủ đề<textarea name="topic" maxLength={3000} placeholder="Mô tả ý tưởng hoặc định hướng nội dung"/></label></div><div className="module-actions"><button disabled={busy}>＋ Tạo dự án</button></div></form></Card> },
      { id: "detail", label: "Chi tiết dự án", content: <Card className="module-card">{activeProject ? <><h2>{activeProject.title}</h2><form className="module-form" key={`${activeProject.id}:${activeProject.title}:${activeProject.topic}`} onSubmit={e => void saveProject(e, activeProject.id)}><div className="module-scroll"><p className="module-help">Chủ đề này sẽ là đầu vào khi chạy bước tạo video của workflow.</p><label>Tên dự án<input name="title" maxLength={150} required defaultValue={activeProject.title} /></label><label>Chủ đề<textarea name="topic" maxLength={3000} defaultValue={activeProject.topic} placeholder="Mô tả chủ đề và phong cách video" /></label><h3>Video của dự án</h3>{assets.filter(a => a.project_id === activeProject.id && a.content_type === "video/mp4").map(a => <div key={a.id}><p>{a.filename}</p><video controls playsInline preload="metadata" src={`/api/assets/${encodeURIComponent(a.id)}`} style={{ width: "100%", maxWidth: 360 }} /><p><a href={`/api/assets/${encodeURIComponent(a.id)}`}>Tải MP4</a></p></div>)}{!assets.some(a => a.project_id === activeProject.id && a.content_type === "video/mp4") && <p className="module-help">Chưa có video. Tạo workflow rồi chạy với dự án này.</p>}</div><div className="module-actions"><button disabled={busy}>{busy ? "Đang lưu…" : "Lưu dự án"}</button></div></form></> : <Empty>Chọn một dự án trong danh sách để xem và sửa chủ đề.</Empty>}</Card> },
    ]} />}
    {page === "library" && <ModuleTabs split activeTab={libraryView} onTabChange={setLibraryView} tabs={[
      { id: "media", label: `Thư viện (${assets.length})`, content: <Card className="module-card"><div className="card-head"><h2>Thư viện</h2><span className="tag">{shownAssets.length} KẾT QUẢ</span></div><div className="library-filters"><input aria-label="Tìm tên tệp" value={mediaSearch} onChange={e => setMediaSearch(e.target.value)} placeholder="Tìm theo tên tệp…"/><select aria-label="Lọc loại media" value={mediaType} onChange={e => setMediaType(e.target.value)}><option value="all">Tất cả</option><option value="image">Ảnh</option><option value="video">Video</option><option value="audio">Âm thanh</option></select></div><div className="module-scroll" tabIndex={0} aria-label="Tệp media">{shownAssets.length ? shownAssets.map(a => <div className="list-row" key={a.id}><div className="row-icon">{a.content_type.startsWith("video/")?"▶":a.content_type.startsWith("audio/")?"♫":"▧"}</div><div><a href={`/api/assets/${encodeURIComponent(a.id)}`}>{a.filename}</a><small>{a.content_type} · {(a.bytes/1048576).toFixed(2)} MB</small></div><a className="asset-link" href={`/api/assets/${encodeURIComponent(a.id)}`}>Tải xuống ↗</a></div>) : <Empty>{assets.length ? "Không tìm thấy tệp phù hợp." : "Kho media còn trống."}</Empty>}</div></Card> },
      { id: "upload", label: "Tải media", content: <Card className="module-card"><h2>Tải media vào studio</h2><form className="module-form" onSubmit={e => formSubmit(e,"assets")}><div className="module-scroll"><p className="module-help">Ảnh, video hoặc âm thanh · tối đa 100 MB mỗi tệp.</p><label>Chọn tệp<input name="file" type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/webm,audio/mpeg,audio/wav,audio/ogg" required/></label><p className="module-help library-upload-note">{assets.length} tệp đang lưu. Xem trước ảnh, video và âm thanh trong Control Room. Gắn tag và ghép cảnh nằm trong lộ trình.</p></div><div className="module-actions"><button disabled={busy}>↑ Tải lên kho</button></div></form></Card> },
    ]} />}
    {page === "workflows" && <div className="workflow-module"><div className="workflow-picker-bar"><label htmlFor="workflow-select"><span>SƠ ĐỒ ĐANG XEM</span><select id="workflow-select" value={activeWorkflow?.id ?? ""} onChange={e => { setSelectedWorkflow(e.target.value); setWorkflowDirty(false); setRunSteps({}); }} disabled={!workflows.length}>{workflows.length ? workflows.map(w => <option key={w.id} value={w.id}>{w.name}</option>) : <option value="">Chưa có workflow</option>}</select></label><span>{workflows.length} workflow đã lưu</span></div><ModuleTabs activeTab={workflowView} onTabChange={setWorkflowView} tabs={[
      { id: "graph", label: "Sơ đồ", content: activeWorkflow ? <FlowEditor key={activeWorkflow.id} workflow={activeWorkflow} onSave={graph => saveGraph(activeWorkflow.id,graph)} onDirtyChange={setWorkflowDirty} runSteps={runSteps}/> : <Card className="module-card"><Empty>Tạo workflow để mở sơ đồ node đầu tiên.</Empty><button type="button" className="primary-action" onClick={() => setWorkflowView("create")}>Tạo workflow</button></Card> },
      { id: "runs", label: "Chạy & lịch sử", content: activeWorkflow ? <WorkflowRuns key={activeWorkflow.id} workflowId={activeWorkflow.id} projects={projects} hasVideo={activeWorkflow.graph.nodes.some(node => node.type === "video")} dirty={workflowDirty} onInspect={inspectRun}/> : <Card className="module-card"><Empty>Tạo workflow trước khi chạy.</Empty></Card> },
      { id: "create", label: "Tạo workflow", content: <Card className="module-card workflow-create-card"><h2>Tạo workflow mới</h2><form className="module-form" onSubmit={async e => {e.preventDefault();const form=e.currentTarget;const name=String(new FormData(form).get("name") ?? "");setBusy(true);setError("");try{const result=await api<Workflow>("workflows",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name})});setSelectedWorkflow(result.id);setWorkflowDirty(false);setRunSteps({});await refresh();form.reset();setWorkflowView("graph")}catch(err){setError(err instanceof Error?err.message:String(err))}finally{setBusy(false)}}}><div className="module-scroll"><p className="module-help">Đặt tên rồi thêm các bước trong trình chỉnh sửa sơ đồ.</p><label>Tên workflow<input name="name" maxLength={150} required placeholder="Tên workflow…"/></label></div><div className="module-actions"><button disabled={busy}>＋ Tạo workflow</button></div></form></Card> },
    ]} /></div>}
    {page === "ai" && <AIToolsPanel />}
    {page === "channels" && <ChannelsPanel />}
    {page === "calendar" && <Planned title="Lịch đăng" description="Chọn thời điểm và trạng thái từng bài đăng sau khi publisher hoạt động. Hiện chưa có lịch hoặc job đăng tải." items={["Lịch theo tháng", "Hàng đợi đăng", "Lịch sử đăng"]}/>}
    {page === "analytics" && <Planned title="Phân tích" description="Theo dõi video đã đăng, hiệu suất từng kênh, mức sử dụng AI và chi phí. Chưa có dữ liệu hoặc đồng bộ analytics." items={["Hiệu suất video", "Sử dụng AI", "Chi phí và credits"]}/>}
    {page === "settings" && <ModuleTabs split tabs={[
      { id: "workspace", label: "Workspace", content: <Card className="module-card"><h2>Workspace: {workspace.name}</h2>{settings ? <form className="module-form" key={JSON.stringify(settings.workspace)} onSubmit={e => saveSettings(e, "workspace")}><div className="module-scroll"><p className="module-help">Gói {workspace.plan.toUpperCase()} · ID {workspace.id}</p><label>Ngôn ngữ mặc định<select name="default_language" defaultValue={settings.workspace.default_language}><option value="vi">Tiếng Việt</option><option value="en">English</option><option value="ja">日本語</option></select></label><label>Tỷ lệ video<select name="video_orientation" defaultValue={settings.workspace.video_orientation}><option value="vertical">Dọc (9:16)</option><option value="horizontal">Ngang (16:9)</option><option value="square">Vuông (1:1)</option></select></label><label className="check-label"><input name="approval_required" type="checkbox" defaultChecked={settings.workspace.approval_required} /> Yêu cầu duyệt trước khi đăng</label><p className="module-help">Luồng tải video lên YouTube hiện luôn yêu cầu chủ workspace duyệt clip, kể cả khi tắt tùy chọn này.</p></div><div className="module-actions"><button disabled={busy}>{busy ? "Đang lưu…" : "Lưu cài đặt workspace"}</button></div></form> : <Spinner label="Đang tải cài đặt…" />}</Card> },
      { id: "system", label: "Hệ thống", content: <Card className="module-card"><h2>Cài đặt hệ thống</h2>{settings?.system && data.is_admin ? <form className="module-form" key={JSON.stringify(settings.system)} onSubmit={e => saveSettings(e, "system")}><div className="module-scroll"><label>Địa chỉ frontend<input name="frontend_origin" type="url" required defaultValue={settings.system.frontend_origin} /></label><label>Giới hạn dự án Trial<input name="trial_project_limit" type="number" min={1} max={10000} required defaultValue={settings.system.trial_project_limit} /></label><label className="check-label"><input name="secure_cookies" type="checkbox" defaultChecked={settings.system.secure_cookies} /> Cookie chỉ qua HTTPS</label><label className="check-label"><input name="registration_enabled" type="checkbox" defaultChecked={settings.system.registration_enabled} /> Cho phép đăng ký tài khoản Trial</label><p className="hint">Thư mục media: {settings.system.storage_dir}. Chuyển dữ liệu media cần thực hiện trên server.</p></div><div className="module-actions"><button disabled={busy}>Lưu cài đặt hệ thống</button></div></form> : <p className="subtle">Chỉ quản trị viên được xem và chỉnh sửa cài đặt hệ thống.</p>}</Card> },
    ]} />}
    </div></div></main></div>;
}
