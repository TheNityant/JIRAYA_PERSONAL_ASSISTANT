import type { AutomationInput, ConnectionCatalogItem } from "@/lib/api";

export const services = [
  {id: "gmail", label: "Gmail", description: "Incoming mail", color: "#ed978d"},
  {id: "google_calendar", label: "Google Calendar", description: "Event changes", color: "#93b7ef"},
  {id: "github", label: "GitHub", description: "Repository activity", color: "#b7acf3"},
] as const;
export const actionLabels: Record<string, string> = {DRAFT_REPLY: "Reply to email", NOTIFY: "Notify me", CREATE_CALENDAR_EVENT: "Create calendar event", CREATE_GITHUB_ISSUE: "Create GitHub issue", CREATE_GITHUB_BRANCH: "Create GitHub branch"};
export function actionTarget(action: string): string {
  return action === "CREATE_CALENDAR_EVENT" ? "google_calendar" : action.startsWith("CREATE_GITHUB_") ? "github" : action === "NOTIFY" ? "notify" : "gmail";
}
export function isConnected(connections: ConnectionCatalogItem[], id: string) {return connections.some((item) => item.id === id && item.status === "CONNECTED");}
export function actionsFor(source: string, connections: ConnectionCatalogItem[]) {
  return Object.keys(actionLabels).filter((action) => action === "NOTIFY" || (source === "gmail" && isConnected(connections, actionTarget(action))));
}
export function routeReady(draft: AutomationInput, connections: ConnectionCatalogItem[]) {
  return isConnected(connections, draft.source) && actionsFor(draft.source, connections).includes(String(draft.action_config.type || "DRAFT_REPLY"));
}
