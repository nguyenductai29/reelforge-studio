import { messages } from "../lib/messages";
export default function Spinner({ label = messages.loading, fullPage = false }: { label?: string; fullPage?: boolean }) {
  return <span className={`spinner-wrap ${fullPage ? "spinner-page" : ""}`} role="status" aria-live="polite"><span className="spinner" aria-hidden="true" />{label}</span>;
}
