import type { CSSProperties } from "react";

const paths = {
  dashboard: ["M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"],
  projects: ["M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7Z"],
  library: ["M4 3h16v18H4zM4 16l5-5 4 4 3-3 4 4", "M15 7h.01"],
  workflows: ["M3 3h6v6H3zM15 15h6v6h-6zM6 9v9h9M9 6h9v9"],
  ai: ["m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"],
  channels: ["M7 17 19 5M7 5h12v12M4 11v9h9"],
  calendar: ["M4 5h16v16H4zM8 3v4M16 3v4M4 11h16M8 15h2M14 15h2"],
  analytics: ["M4 3v18h17M8 16v-5M13 16V7M18 16V4"],
  settings: ["M4 7h16M4 17h16M8 4v6M16 14v6"],
  billing: ["M3 5h18v14H3zM3 10h18M7 15h3"],
  admin: ["m12 3 8 3v6c0 5-8 9-8 9S4 17 4 12V6l8-3Z", "m9 12 2 2 4-4"],
  plus: ["M12 5v14M5 12h14"],
  search: ["M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15ZM16 16l5 5"],
  arrow: ["M5 12h14m-5-5 5 5-5 5"],
  video: ["m9 7 8 5-8 5V7Z", "M3 3h18v18H3z"],
  audio: ["M9 18V5l11-2v13M9 18a3 3 0 1 1-3-3h3M20 16a3 3 0 1 1-3-3h3"],
  menu: ["M4 6h16M4 12h16M4 18h16"],
  close: ["m6 6 12 12M6 18 18 6"],
  logout: ["M10 4H4v16h6M9 12h12m-4-4 4 4-4 4"],
} as const;

export type StudioIconName = keyof typeof paths;
export default function StudioIcon({ name, size = 20, style }: { name: StudioIconName; size?: number; style?: CSSProperties }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style}>{paths[name].map((d, index) => <path key={index} d={d} />)}</svg>;
}
