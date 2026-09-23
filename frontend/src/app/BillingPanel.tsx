"use client";
import { useCallback, useEffect, useState } from "react";
import { api, jsonRequest } from "../lib/api";
import { errorMessage } from "../lib/messages";
import Message from "../components/Message";
import Spinner from "../components/Spinner";

type Plan = { code: string; name: string; price_vnd: number | null; project_limit: number | null; workflow_limit: number | null; is_active: boolean };
type Order = { id: string; plan_code: string; amount_vnd: number; status: string; created_at: string; paid_at: string | null };
type Billing = { plans: Plan[]; subscription: { plan_code: string; status: string; ends_at: string | null }; orders: Order[]; payos_ready: boolean };
const money = (value: number) => new Intl.NumberFormat("vi-VN", { style: "currency", currency: "VND" }).format(value);
const rank = (code: string) => ["trial", "standard", "pro"].indexOf(code);
export default function BillingPanel({ onPayment }: { onPayment: () => Promise<void> }) {
  const [data, setData] = useState<Billing | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { try { setData(await api<Billing>("billing")); setError(""); } catch (e) { setError(errorMessage(e)); } }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (new URLSearchParams(window.location.search).has("payment")) { void load().then(onPayment); window.history.replaceState(null, "", window.location.pathname); } }, [load, onPayment]);
  async function checkout(code: string) {
    setBusy(true); setError("");
    try {
      const result = await api<{ checkout_url: string }>("billing/checkout", jsonRequest("POST", { plan_code: code }));
      const url = new URL(result.checkout_url);
      if (url.protocol !== "https:") throw new Error("Liên kết thanh toán không hợp lệ");
      window.location.assign(url.toString());
    } catch (e) { setError(errorMessage(e)); setBusy(false); }
  }
  if (!data) return <>{error && <Message>{error}</Message>}<Spinner fullPage /></>;
  return <div className="admin-stack">
    {error && <Message>{error}</Message>}
    <div className="roadmap-banner"><span className="tag">THANH TOÁN</span><h2>Gói {data.subscription.plan_code.toUpperCase()}</h2><p>Trạng thái: {data.subscription.status}{data.subscription.ends_at ? ` · Hết hạn ${new Date(data.subscription.ends_at).toLocaleDateString("vi-VN")}` : ""}. Giá hiển thị cho 30 ngày. Gói mới chỉ kích hoạt sau khi payOS xác nhận thanh toán.</p></div>
    <div className="billing-grid">{data.plans.map(plan => <section className="card" key={plan.code}><span className="tag">{plan.code.toUpperCase()}</span><h2>{plan.name}</h2><strong>{plan.code === "trial" ? "Miễn phí" : plan.price_vnd ? money(plan.price_vnd) : "Chưa mở bán"}</strong><p className="subtle">{plan.project_limit ?? "Không giới hạn"} dự án · {plan.workflow_limit ?? "Không giới hạn"} workflow</p>{rank(plan.code) > rank(data.subscription.plan_code) && <button className="primary-action" disabled={busy || !plan.price_vnd || !data.payos_ready || data.subscription.status !== "active"} onClick={() => void checkout(plan.code)}>{busy ? <Spinner label="Đang tạo đơn…" /> : "Thanh toán VNQR"}</button>}</section>)}</div>
    {!data.payos_ready && <Message>Chưa kết nối tài khoản payOS của studio. Admin cần cấu hình thông tin merchant trên server.</Message>}
    <section className="card"><div className="card-head"><h2>Lịch sử thanh toán</h2><button className="text-button" onClick={() => void load()}>Làm mới ↻</button></div><p className="subtle">Quay lại trang sau khi thanh toán không xác nhận đơn. Trạng thái chỉ đổi khi backend nhận webhook hợp lệ.</p>{data.orders.length ? data.orders.map(order => <div className="list-row" key={order.id}><div className="row-icon">◈</div><div><b>{order.plan_code.toUpperCase()} · {money(order.amount_vnd)}</b><small>{new Date(order.created_at).toLocaleString("vi-VN")}</small></div><span className="status">{order.status}</span></div>) : <div className="empty">Chưa có đơn thanh toán.</div>}</section>
    <p className="hint">Thanh toán bằng thẻ đang chờ kết nối merchant OnePAY. Crypto chưa được bật.</p>
  </div>;
}
