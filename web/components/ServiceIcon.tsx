import type { ReactNode } from "react";

export default function ServiceIcon({service, size = 20}: {service: string; size?: number}) {
  const paths: Record<string, ReactNode> = {
    meet: <><rect x="2" y="5" width="14" height="14" rx="3"/><path d="m16 10 6-4v12l-6-4"/></>,
    gmail: <><rect x="3" y="5" width="18" height="14" rx="3"/><path d="m4 7 8 6 8-6"/></>,
    google_calendar: <><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4m10-4v4M3 11h18m-13 4h2m4 0h2m-8 3h2"/></>,
    github: <><circle cx="7" cy="5" r="2"/><circle cx="7" cy="19" r="2"/><circle cx="17" cy="5" r="2"/><path d="M7 7v10m10-10v3a4 4 0 0 1-4 4H7"/></>,
    notify: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9m-11 12h4"/></>,
    logic: <><circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><path d="m6 8 5 8m7-8-5 8M7 6h10"/></>,
    check: <><path d="m5 12 4 4L19 6"/></>,
    refresh: <><path d="M20 8a8 8 0 1 0 0 8m0-13v5h-5"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[service] || paths.logic}</svg>;
}
