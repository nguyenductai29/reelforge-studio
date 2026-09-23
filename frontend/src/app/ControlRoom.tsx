"use client";

import { useState, type KeyboardEvent } from "react";
import type { Workflow } from "./FlowEditor";
import PortraitPreview from "./PortraitPreview";
import StudioIcon from "../components/StudioIcon";

type Asset = { id: string; filename: string; content_type: string; bytes: number };
type Project = { id: string; title: string; topic: string; status: string };
type Destination = "projects" | "library" | "workflows" | "ai" | "billing";
type Props = {
  assets: Asset[];
  projects: Project[];
  workflows: Workflow[];
  projectLimit: number | null;
  onNavigate: (page: Destination, view?: string) => void;
  onOpenWorkflow: (id: string) => void;
};
const nodeNames: Record<string, string> = { idea: "Ý tưởng", script: "Kịch bản AI", scenes: "Phân cảnh", image: "Tạo ảnh", video: "Tạo clip", assets: "Kho media", voice: "Giọng đọc", music: "Nhạc & SFX", subtitle: "Phụ đề", render: "Render", review: "Duyệt", publish: "Đăng tải" };
const filters = [{ id: "all", label: "Tất cả" }, { id: "video", label: "Video" }, { id: "image", label: "Ảnh" }, { id: "audio", label: "Audio" }];

export default function ControlRoom({ assets, projects, workflows, projectLimit, onNavigate, onOpenWorkflow }: Props) {
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [panel, setPanel] = useState("preview");
  const [sourcePanel, setSourcePanel] = useState("media");
  const panelTabs = [{ id: "preview", label: "Preview" }, { id: "media", label: "Media" }, { id: "workflow", label: "Workflow" }];
  function changeTab(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = event.key === "ArrowRight" ? (index + 1) % panelTabs.length : event.key === "ArrowLeft" ? (index + panelTabs.length - 1) % panelTabs.length : event.key === "Home" ? 0 : event.key === "End" ? panelTabs.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    setPanel(panelTabs[next].id);
    document.getElementById(`cr-tab-${panelTabs[next].id}`)?.focus();
  }
  const selectedAsset = assets.find(asset => asset.id === sourceId) ?? assets.find(asset => /^(image|video)\//.test(asset.content_type)) ?? assets[0] ?? null;
  const workflow = workflows.find(item => item.id === workflowId) ?? workflows[0];
  const sources = assets.filter(asset => (filter === "all" || asset.content_type.startsWith(`${filter}/`)) && asset.filename.toLocaleLowerCase("vi").includes(search.toLocaleLowerCase("vi")));

  return <div className="control-room">
    <div className="cr-heading">
      <div><p className="cr-eyebrow"><span /> KHÔNG GIAN SẢN XUẤT</p><h1 tabIndex={-1}>Broadcast <span>Control Room</span></h1><p>Nguồn media, khung hình và quy trình. Trong cùng một góc nhìn.</p></div>
      <button type="button" className="cr-primary" aria-label="Tạo dự án" title="Tạo dự án" onClick={() => onNavigate("projects", "create")}><StudioIcon name="plus" size={17} /><span>Tạo dự án</span></button>
    </div>

    <div className="cr-view-tabs" role="tablist" aria-label="Bảng điều khiển">{panelTabs.map((tab, index) => <button key={tab.id} id={`cr-tab-${tab.id}`} type="button" role="tab" aria-selected={panel === tab.id} aria-controls={`cr-panel-${tab.id}`} tabIndex={panel === tab.id ? 0 : -1} onClick={() => setPanel(tab.id)} onKeyDown={event => changeTab(event, index)}>{tab.label}</button>)}</div>

    <div className="cr-grid">
      <div id="cr-panel-media" className={`cr-source-column cr-tab-panel ${panel === "media" ? "is-active" : ""}`} role="tabpanel" aria-labelledby="cr-tab-media">
        <div className="cr-source-switch" role="group" aria-label="Nguồn và dự án"><button type="button" aria-pressed={sourcePanel === "media"} onClick={() => setSourcePanel("media")}>Nguồn media <span>{assets.length}</span></button><button type="button" aria-pressed={sourcePanel === "projects"} onClick={() => setSourcePanel("projects")}>Dự án <span>{projects.length}</span></button></div>
        <section className="cr-panel cr-sources" aria-labelledby="cr-sources-title" hidden={sourcePanel !== "media"}>
          <div className="cr-panel-heading"><div><span className="cr-section-number">01</span><h2 id="cr-sources-title">Nguồn media</h2></div><span className="cr-count">{assets.length}</span></div>
          <div className="cr-source-tools"><div className="cr-search"><StudioIcon name="search" size={16} /><input id="cr-media-search" aria-label="Tìm nguồn media" placeholder="Tìm tệp trong studio…" value={search} onChange={event => setSearch(event.target.value)} /></div><div className="cr-filters" role="group" aria-label="Loại nguồn media">{filters.map(item => <button type="button" key={item.id} aria-pressed={filter === item.id} onClick={() => setFilter(item.id)}>{item.label}</button>)}</div></div>
          <div className="cr-source-list">
            {sources.length ? sources.map(asset => <button type="button" className={`cr-source-item ${selectedAsset?.id === asset.id ? "is-selected" : ""}`} key={asset.id} aria-pressed={selectedAsset?.id === asset.id} onClick={() => { setSourceId(asset.id); setPanel("preview"); if (window.matchMedia("(max-width: 1050px)").matches) document.getElementById("cr-tab-preview")?.focus(); }} title={asset.filename}>
              <span className="cr-source-thumb"><StudioIcon name={asset.content_type.startsWith("image/") ? "library" : asset.content_type.startsWith("video/") ? "video" : "audio"} size={20} /></span><span className="cr-source-name"><b>{asset.filename}</b><small>{asset.content_type.split("/")[1]?.toUpperCase()} <span>·</span> {(asset.bytes / 1048576).toFixed(1)} MB</small></span><span className="cr-source-indicator" />
            </button>) : <div className="cr-empty"><StudioIcon name={assets.length ? "search" : "library"} size={28} /><b>{assets.length ? "Không tìm thấy tệp" : "Bắt đầu từ nguồn media"}</b><p>{assets.length ? "Thử tên khác hoặc đổi bộ lọc." : "Thêm ảnh, video hoặc âm thanh để xem trước tại đây."}</p>{!assets.length && <button type="button" className="cr-text-button" onClick={() => onNavigate("library")}>Mở kho media <StudioIcon name="arrow" size={15} /></button>}</div>}
          </div>
          <button type="button" className="cr-add-source" onClick={() => onNavigate("library", "upload")}><StudioIcon name="plus" size={16} /> Thêm media <span>↗</span></button>
        </section>
        <section className="cr-panel cr-projects" aria-labelledby="cr-projects-title" hidden={sourcePanel !== "projects"}><div className="cr-panel-heading"><h2 id="cr-projects-title">Dự án gần đây</h2><span className="cr-count">{projects.length}</span></div><div className="cr-project-list">{projects.length ? projects.map(project => <button type="button" className="cr-project-row" key={project.id} onClick={() => onNavigate("projects")} title={project.title}><StudioIcon name="projects" size={17} /><span><b>{project.title}</b><small>{project.status === "draft" ? "Bản nháp" : project.status}</small></span></button>) : <p className="cr-project-empty">Ý tưởng tiếp theo của bạn bắt đầu bằng một dự án mới.</p>}</div><button type="button" className="cr-add-source" onClick={() => onNavigate("projects")}>Quản lý dự án <StudioIcon name="arrow" size={16} /></button></section>
      </div>

      <div id="cr-panel-preview" className={`cr-monitor-column cr-tab-panel ${panel === "preview" ? "is-active" : ""}`} role="tabpanel" aria-labelledby="cr-tab-preview"><PortraitPreview key={selectedAsset?.id ?? "empty"} asset={selectedAsset} onOpenLibrary={() => {
        if (!assets.length) { onNavigate("library", "upload"); return; }
        setSourcePanel("media"); setPanel("media");
        requestAnimationFrame(() => document.getElementById(window.matchMedia("(max-width: 1050px)").matches ? "cr-tab-media" : "cr-media-search")?.focus());
      }} /></div>

      <aside id="cr-panel-workflow" className={`cr-inspector-column cr-tab-panel ${panel === "workflow" ? "is-active" : ""}`} role="tabpanel" aria-labelledby="cr-tab-workflow">
        <section className="cr-panel cr-workflow"><div className="cr-panel-heading"><div><span className="cr-section-number">03</span><h2>Quy trình sản xuất</h2></div><StudioIcon name="workflows" size={17} /></div>
          <div className="cr-workflow-content"><div className="cr-workflow-picker"><label htmlFor="cr-workflow-select" className="cr-field-label">WORKFLOW ĐANG XEM</label><select id="cr-workflow-select" value={workflow?.id ?? ""} onChange={event => setWorkflowId(event.target.value)} disabled={!workflows.length}>{workflows.length ? workflows.map(item => <option key={item.id} value={item.id}>{item.name}</option>) : <option value="">Chưa có workflow</option>}</select></div>
            {workflow ? <><div className="cr-workflow-scroll"><div className="cr-workflow-meta"><span>{workflow.graph.nodes.length} bước</span><span>{workflow.graph.edges.length} kết nối</span><span className="cr-saved">Đã lưu</span></div><ul className="cr-node-list">{workflow.graph.nodes.map((node, index) => <li key={node.id}><span className="cr-node-index">{String(index + 1).padStart(2, "0")}</span><span>{nodeNames[node.type] ?? node.type}</span><span className="cr-node-port" /></li>)}</ul>{!workflow.graph.nodes.length && <p className="cr-note">Thêm các bước đầu tiên trong trình chỉnh sửa workflow.</p>}<p className="cr-note">Các bước trong sơ đồ đã lưu. Mở lịch sử để xem kết quả từng lần chạy.</p></div></> : <div className="cr-empty cr-workflow-empty cr-workflow-scroll"><StudioIcon name="workflows" size={28} /><b>Thiết kế luồng sản xuất</b><p>Kết nối ý tưởng, media và các bước hoàn thiện video.</p></div>}
            <div className="cr-workflow-actions"><button type="button" className="cr-secondary cr-full" onClick={() => workflow ? onOpenWorkflow(workflow.id) : onNavigate("workflows", "create")}>{workflow ? "Mở sơ đồ & lịch sử chạy" : "Tạo workflow"}<StudioIcon name="arrow" size={16} /></button><button type="button" className="cr-text-button cr-ai-action" aria-label="Cấu hình công cụ AI" title="Cấu hình công cụ AI" onClick={() => onNavigate("ai")}><StudioIcon name="ai" size={16} /><span>Công cụ AI</span></button></div>
          </div>
        </section>
      </aside>
    </div>

    <footer className="cr-overview" aria-label="Thống kê studio"><button type="button" onClick={() => onNavigate("projects")}><strong>{projects.length}</strong><span>Dự án</span></button><button type="button" onClick={() => onNavigate("library")}><strong>{assets.length}</strong><span>Media</span></button><button type="button" onClick={() => onNavigate("workflows")}><strong>{workflows.length}</strong><span>Workflow</span></button><button type="button" className="cr-quota" onClick={() => onNavigate("billing")} title="Xem gói và giới hạn dự án"><span>Gói studio</span><b>{projectLimit === null ? "Không giới hạn" : `${projects.length} / ${projectLimit} dự án`}</b></button></footer>
  </div>;
}
