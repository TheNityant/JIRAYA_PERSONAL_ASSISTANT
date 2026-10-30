import type { ServiceStatus } from "@/lib/api";
export default function ServiceMonitor({service, label}: {service: ServiceStatus | null; label: string}) {
  const monitor = service?.monitor;
  const noWork = monitor?.last_decision === "NO_AUTOMATIONS";
  const error = monitor?.last_status === "ERROR";
  const stale = Boolean(service?.background_monitor_configured && monitor?.last_poll_at && monitor.fresh === false);
  const active = service?.connected && service.background_monitor_configured && !error && !noWork && monitor?.operational === true;
  return <div className={`monitor-card ${error || stale ? "warning" : ""}`}><span className={`monitor-dot ${active ? "live" : ""}`}/><div><strong>{!service?.connected ? "Account not connected" : stale ? "Monitoring has stopped checking" : error ? "Connection needs attention" : noWork ? "No watchers configured" : active ? `${label} is monitoring` : "Monitoring is idle"}</strong><p>{stale ? "No recent check has completed. The automation service may be asleep, offline, or delayed." : error ? monitor?.last_error : noWork ? "Create an automation to start watching changes." : active ? `Checks every ${service?.poll_seconds}s · Last checked ${monitor?.last_poll_at ? new Date(monitor.last_poll_at).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "recently"}` : "Your saved automations control what is watched."}</p></div></div>;
}
