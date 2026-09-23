"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

type Tab = { id: string; label: string; content: ReactNode };
type Props = {
  tabs: Tab[];
  initialTab?: string;
  className?: string;
  activeTab?: string;
  onTabChange?: (id: string) => void;
  split?: boolean;
};

/** Panels stay mounted so changing views never discards a form or an unsaved graph. */
export default function ModuleTabs({ tabs, initialTab, className = "", activeTab, onTabChange, split = false }: Props) {
  const [selected, setSelected] = useState(initialTab ?? tabs[0]?.id);
  const [compact, setCompact] = useState(true);
  const id = useId();
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const current = tabs.find(tab => tab.id === (activeTab ?? selected))?.id ?? tabs[0]?.id;
  const showColumns = split && !compact;

  useEffect(() => {
    if (!split) return;
    const query = window.matchMedia("(max-width: 1000px), (max-height: 500px)");
    const update = () => setCompact(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, [split]);

  function select(tabId: string) { setSelected(tabId); onTabChange?.(tabId); }

  return <div className={`module-tabs ${showColumns ? "is-split" : ""} ${className}`}>
    <div className="module-tab-list" role="tablist" aria-label="Nội dung module" hidden={showColumns}>
      {tabs.map((tab, index) => <button key={tab.id} ref={element => { buttons.current[index] = element; }} type="button" role="tab" id={`${id}-tab-${tab.id}`} aria-selected={current === tab.id} aria-controls={`${id}-panel-${tab.id}`} tabIndex={current === tab.id ? 0 : -1} onClick={() => select(tab.id)} onKeyDown={event => {
        let next = index;
        if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
        else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = tabs.length - 1;
        else return;
        event.preventDefault();
        select(tabs[next].id);
        buttons.current[next]?.focus();
      }}>{tab.label}</button>)}
    </div>
    {tabs.map(tab => <div key={tab.id} id={`${id}-panel-${tab.id}`} className="module-tab-panel" role={showColumns ? "region" : "tabpanel"} aria-label={showColumns ? tab.label : undefined} aria-labelledby={showColumns ? undefined : `${id}-tab-${tab.id}`} hidden={!showColumns && current !== tab.id}>{tab.content}</div>)}
  </div>;
}
