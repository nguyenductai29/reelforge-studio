"use client";
import { useEffect } from "react";
import { messages } from "../lib/messages";
export default function ConfirmDialog({ title, description, busy = false, onConfirm, onClose }: { title: string; description: string; busy?: boolean; onConfirm: () => void; onClose: () => void }) {
  useEffect(() => { const escape = (event: KeyboardEvent) => { if (event.key === "Escape" && !busy) onClose(); }; document.addEventListener("keydown", escape); return () => document.removeEventListener("keydown", escape); }, [onClose, busy]);
  return <div className="dialog-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose(); }}><div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="dialog-title" aria-describedby="dialog-description"><h2 id="dialog-title">{title}</h2><p id="dialog-description">{description}</p><div className="dialog-actions"><button type="button" onClick={onClose} disabled={busy}>{messages.cancel}</button><button type="button" className="primary-action" onClick={onConfirm} disabled={busy}>{busy ? messages.saving : messages.confirm}</button></div></div></div>;
}
