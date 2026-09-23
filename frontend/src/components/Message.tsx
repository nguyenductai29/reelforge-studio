import type { ReactNode } from "react";
export default function Message({ children, tone = "error" }: { children: ReactNode; tone?: "error" | "success" }) {
  return <div className={`message message-${tone}`} role={tone === "error" ? "alert" : "status"}>{children}</div>;
}
