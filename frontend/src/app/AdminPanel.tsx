"use client";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, jsonRequest } from "../lib/api";
import { errorMessage, messages } from "../lib/messages";
import ConfirmDialog from "../components/ConfirmDialog";
import Message from "../components/Message";
import Spinner from "../components/Spinner";
import ModuleTabs from "../components/ModuleTabs";
import "./management-viewport.css";

type Plan = { code: string; name: string; project_limit: number | null; workflow_limit: number | null; monthly_credits: number; is_active: boolean; price_vnd: number | null };
type User = { id: string; email: string; is_admin: boolean; is_active: boolean };
type Workspace = { id: string; name: string; owner_id: string; plan_code: string | null; status: string; ends_at: string | null; credits: number };
type Overview = { users: User[]; workspaces: Workspace[]; plans: Plan[] };
const limit = (n: number | null) => n === null ? "Không giới hạn" : String(n);

export default function AdminPanel() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<{ title: string; description: string; run: () => Promise<boolean> } | null>(null);
  const load = useCallback(async () => { try { setOverview(await api<Overview>("admin")); } catch (e) { setError(errorMessage(e)); } }, []);
  useEffect(() => { void load(); }, [load]);
  async function mutate(run: () => Promise<unknown>, text: string = messages.saved): Promise<boolean> {
    setBusy(true); setError(""); setSuccess("");
    try { await run(); await load(); setSuccess(text); return true; } catch (e) { setError(errorMessage(e)); return false; } finally { setBusy(false); setConfirm(null); }
  }
  async function createAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const fields = new FormData(form);
    if (await mutate(() => api("admin/accounts", jsonRequest("POST", Object.fromEntries(fields))), messages.created)) form.reset();
  }
  if (!overview) return <>{error && <Message>{error}</Message>}<Spinner /></>;
  const owner = (id: string) => overview.users.find(user => user.id === id)?.email ?? id;
  return <div className="management-viewport admin-viewport">
    {error && <Message>{error}</Message>}{success && <Message tone="success">{success}</Message>}
    <div className="admin-summary"><div className="card"><small>NGƯỜI DÙNG</small><strong>{overview.users.length}</strong></div><div className="card"><small>STUDIO</small><strong>{overview.workspaces.length}</strong></div><div className="card"><small>GÓI DỊCH VỤ</small><strong>{overview.plans.length}</strong></div></div>
    <ModuleTabs tabs={[
      { id: "users", label: "Người dùng", content: <section className="card management-panel">
        <div className="management-panel-heading"><h2>Người dùng</h2><p className="subtle">Quản lý quyền truy cập của {overview.users.length} tài khoản.</p></div>
        <div className="admin-list management-list module-scroll">{overview.users.map(user => <div className="admin-row" key={user.id}>
          <div><b>{user.email}</b><small>{user.is_admin ? "Quản trị hệ thống" : "Người dùng"} · {user.is_active ? "Đang hoạt động" : "Đã khóa"}</small></div>
          <button type="button" disabled={busy} onClick={() => setConfirm({ title: user.is_active ? "Khóa tài khoản?" : "Mở khóa tài khoản?", description: `${user.email} ${user.is_active ? "sẽ bị đăng xuất ngay." : "sẽ có thể đăng nhập lại."}`, run: () => mutate(() => api(`admin/users/${user.id}`, jsonRequest("PUT", { is_active: !user.is_active }))) })}>{user.is_active ? "Khóa" : "Mở khóa"}</button>
        </div>)}</div>
      </section> },
      { id: "create", label: "Tạo tài khoản", content: <section className="card management-panel">
        <div className="management-panel-heading"><h2>Tạo tài khoản và studio</h2><p className="subtle">Quản trị viên cấp tài khoản trực tiếp. Gửi thông tin đăng nhập cho người dùng bằng kênh riêng.</p></div>
        <form className="management-form" onSubmit={createAccount}>
          <div className="management-form-fields management-field-grid module-scroll">
            <label>Email<input type="email" name="email" required /></label>
            <label>Mật khẩu ban đầu<input type="password" name="password" minLength={12} required autoComplete="new-password" /></label>
            <label>Tên studio<input name="workspace_name" maxLength={100} required /></label>
            <label>Gói<select name="plan_code">{overview.plans.filter(p => p.is_active).map(p => <option key={p.code} value={p.code}>{p.name}</option>)}</select></label>
          </div>
          <div className="management-form-actions"><button disabled={busy}>{busy ? <Spinner label={messages.saving} /> : "＋ Tạo tài khoản"}</button></div>
        </form>
      </section> },
      { id: "studios", label: "Studio & credits", content: <section className="card management-panel">
        <div className="management-panel-heading"><h2>Studio và subscription</h2><p className="subtle">Đổi gói là thao tác quản trị thủ công; chưa tích hợp thanh toán.</p></div>
        <div className="admin-list management-list module-scroll">{overview.workspaces.map(ws => <div className="admin-row" key={ws.id}>
          <div><b>{ws.name}</b><small>{owner(ws.owner_id)} · {ws.credits} credits · {ws.status}{ws.ends_at ? ` · Hết hạn ${new Date(ws.ends_at).toLocaleDateString("vi-VN")}` : ""}</small></div>
          <select aria-label={`Gói của ${ws.name}`} value={ws.plan_code ?? ""} disabled={busy} onChange={event => { const code = event.target.value; setConfirm({ title: "Đổi gói studio?", description: `${ws.name}: ${ws.plan_code} → ${code}. Hạn mức mới áp dụng ngay.`, run: () => mutate(() => api(`admin/workspaces/${ws.id}/subscription`, jsonRequest("PUT", { plan_code: code, status: ws.status === "active" ? "active" : "paused" }))) }); }}>{overview.plans.filter(p => p.is_active || p.code === ws.plan_code).map(p => <option key={p.code} value={p.code}>{p.name}</option>)}</select>
          <button disabled={busy} onClick={() => setConfirm({ title: ws.status === "active" ? "Tạm dừng studio?" : "Kích hoạt studio?", description: `${ws.name} ${ws.status === "active" ? "sẽ không thể tạo dự án hoặc tải media." : "sẽ hoạt động trở lại."}`, run: () => mutate(() => api(`admin/workspaces/${ws.id}/subscription`, jsonRequest("PUT", { plan_code: ws.plan_code, status: ws.status === "active" ? "paused" : "active" }))) })}>{ws.status === "active" ? "Tạm dừng" : "Kích hoạt"}</button>
          <form className="credit-adjust" onSubmit={event => { event.preventDefault(); const form = event.currentTarget; const values = new FormData(form); void mutate(() => api(`admin/workspaces/${ws.id}/credits`, jsonRequest("POST", { delta: Number(values.get("delta")), reason: values.get("reason") }))).then(ok => { if (ok) form.reset(); }); }}>
            <input type="number" name="delta" min={-1000000} max={1000000} required placeholder="± credits" aria-label={`Điều chỉnh credits ${ws.name}`} />
            <input name="reason" minLength={3} maxLength={80} required placeholder="Lý do" aria-label={`Lý do điều chỉnh ${ws.name}`} />
            <button disabled={busy}>Điều chỉnh</button>
          </form>
        </div>)}</div>
      </section> },
      { id: "plans", label: "Cấu hình gói", content: <section className="card management-panel">
        <div className="management-panel-heading"><h2>Cấu hình gói</h2><p className="subtle">Credits là mức dự kiến; hệ thống chưa tiêu credits cho AI hoặc render.</p></div>
        <ModuleTabs className="management-plan-tabs" tabs={overview.plans.map(p => ({ id: p.code, label: p.code.toUpperCase(), content: <form className="management-form" onSubmit={event => {
          event.preventDefault(); const form = new FormData(event.currentTarget);
          void mutate(() => api(`admin/plans/${p.code}`, jsonRequest("PUT", { name: form.get("name"), project_limit: form.get("project_limit") ? Number(form.get("project_limit")) : null, workflow_limit: form.get("workflow_limit") ? Number(form.get("workflow_limit")) : null, monthly_credits: Number(form.get("monthly_credits")), price_vnd: p.code === "trial" ? null : (form.get("price_vnd") ? Number(form.get("price_vnd")) : null), is_active: form.get("is_active") === "on" })));
        }}>
          <div className="management-form-fields management-field-grid module-scroll">
            <label>Tên gói<input name="name" defaultValue={p.name} required maxLength={80} /></label>
            <label>Giới hạn dự án<input type="number" name="project_limit" min={1} defaultValue={p.project_limit ?? ""} placeholder="Không giới hạn" required={p.code === "trial"} /></label>
            <label>Giới hạn workflow<input type="number" name="workflow_limit" min={1} defaultValue={p.workflow_limit ?? ""} placeholder="Không giới hạn" /></label>
            <label>Giá VND / 30 ngày<input type="number" name="price_vnd" min={2000} max={2000000000} defaultValue={p.price_vnd ?? ""} placeholder={p.code === "trial" ? "Miễn phí" : "Chưa mở bán"} disabled={p.code === "trial"} /></label>
            <label>Credits dự kiến/tháng<input type="number" name="monthly_credits" min={0} defaultValue={p.monthly_credits} required /></label>
            <label className="check-label"><input type="checkbox" name="is_active" defaultChecked={p.is_active} /> Cho phép sử dụng</label>
          </div>
          <div className="management-form-actions"><small>Dự án: {limit(p.project_limit)} · Workflow: {limit(p.workflow_limit)}</small><button disabled={busy}>Lưu gói</button></div>
        </form> }))} />
      </section> },
    ]} />
    {confirm && <ConfirmDialog title={confirm.title} description={confirm.description} busy={busy} onClose={() => setConfirm(null)} onConfirm={() => { void confirm.run(); }} />}
  </div>;
}
