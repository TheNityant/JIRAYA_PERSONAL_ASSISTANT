export type Automation = {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  source: "gmail";
  trigger_type: "NEW_EMAIL";
  match_config: Record<string, unknown>;
  context_config: Record<string, unknown>;
  action_config: Record<string, unknown>;
  approval_policy: "REQUIRED" | "DRAFT_ONLY" | "AUTO";
  system_pipeline: Array<{ type: string; label: string }>;
  created_at: string;
  updated_at: string;
};

export type AutomationInput = Omit<
  Automation,
  "id" | "system_pipeline" | "created_at" | "updated_at"
>;

export type AutomationExecution = {
  id: string;
  automation_id: string;
  correlation_id: string;
  source: string;
  source_event_id: string;
  source_thread_id: string | null;
  action_type: string;
  approval_policy: "REQUIRED" | "DRAFT_ONLY" | "AUTO";
  status: string;
  match_result: Record<string, unknown>;
  draft_body: string;
  output: Record<string, unknown>;
  action_id: string | null;
  created_at: string;
  updated_at: string;
  executed_at: string | null;
};

export type AutomationRunResponse = {
  decision: string;
  automation_id?: string;
  action_id?: string;
  obligation_id?: string;
  verification_status?: string;
  reason?: string;
  action_route?: { decision: string; confidence: number; reason: string };
  message?: {
    id: string;
    thread_id: string;
    from: string;
    subject: string;
    body_preview: string;
  };
  match?: {
    matches: boolean;
    confidence: number;
    reason: string;
    deterministic_checks: Record<string, boolean>;
  };
  execution?: AutomationExecution;
};

export type ConnectionCatalogItem = {
  id: string;
  provider: string;
  label: string;
  description: string;
  status: "AVAILABLE" | "CONNECTED" | "COMING_NEXT";
  account: string | null;
  connect_path: string | null;
};

export type ConnectionsResponse = {
  mode: string;
  user: { id: string; email: string };
  connections: Array<{
    id: string;
    provider: string;
    provider_account_id: string;
    display_name: string;
    status: string;
    scopes: string[];
    token_expires_at: string | null;
    updated_at: string;
  }>;
  catalog: ConnectionCatalogItem[];
};

export type JirayaNotification = {
  id: string;
  category: "ACTION_REQUIRED" | "AUTOMATION_EXECUTED" | "INFORMATION" | "STATE_CHANGED" | string;
  severity: string;
  title: string;
  message: string;
  source: string;
  source_entity_id: string | null;
  automation_id: string | null;
  action_id: string | null;
  obligation_id: string | null;
  metadata: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
};

export type NotificationResponse = {
  unread_count: number;
  items: JirayaNotification[];
};

export type Obligation = {
  id: string;
  obligation_type: string;
  source: string;
  source_entity_id: string;
  related_action_id: string | null;
  external_thread_id: string | null;
  trigger_message_id: string | null;
  status: string;
  title: string;
  description: string;
  context: Record<string, unknown>;
  resolution: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
};

export type GmailRuntimeStatus = {
  background_monitor_enabled: boolean;
  poll_seconds: number;
  pubsub_configured: boolean;
  pubsub_topic: string | null;
  history_cursor: string | null;
  mailbox: string | null;
};

export const API_BASE = process.env.NEXT_PUBLIC_JIRAYA_API_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  listAutomations: () => request<Automation[]>("/automations"),
  createAutomation: (input: AutomationInput) =>
    request<Automation>("/automations", { method: "POST", body: JSON.stringify(input) }),
  updateAutomation: (id: string, input: Partial<AutomationInput>) =>
    request<Automation>(`/automations/${id}`, { method: "PUT", body: JSON.stringify(input) }),
  deleteAutomation: (id: string) => request<void>(`/automations/${id}`, { method: "DELETE" }),
  createInternshipPreset: () =>
    request<Automation>("/automations/presets/internship-assistant", { method: "POST" }),
  previewGmail: (id: string, messageId?: string) =>
    request<any>(`/automations/${id}/preview/gmail`, {
      method: "POST",
      body: JSON.stringify({ message_id: messageId || null }),
    }),
  runGmailAutomation: (id: string, messageId?: string, force = false) =>
    request<AutomationRunResponse>(`/automations/${id}/run/gmail`, {
      method: "POST",
      body: JSON.stringify({ message_id: messageId || null, force }),
    }),
  approveExecution: (executionId: string, draftBody?: string) =>
    request<AutomationRunResponse>(`/automation-executions/${executionId}/approve`, {
      method: "POST",
      body: JSON.stringify({ draft_body: draftBody ?? null }),
    }),
  getExecution: (executionId: string) =>
    request<AutomationExecution>(`/automation-executions/${executionId}`),
  listExecutions: (automationId?: string) =>
    request<AutomationExecution[]>(
      `/automation-executions${automationId ? `?automation_id=${encodeURIComponent(automationId)}` : ""}`,
    ),
  listConnections: () => request<ConnectionsResponse>("/connections"),
  disconnectGoogle: () =>
    request<{ disconnected: boolean; provider: string }>("/connections/google/disconnect", {
      method: "POST",
    }),
  googleConnectUrl: () => `${API_BASE}/connections/google/start`,
  listNotifications: (category?: string) =>
    request<NotificationResponse>(
      `/notifications${category ? `?category=${encodeURIComponent(category)}` : ""}`,
    ),
  markNotificationRead: (id: string) =>
    request<JirayaNotification>(`/notifications/${id}/read`, { method: "POST" }),
  listObligations: (status?: string) =>
    request<Obligation[]>(`/obligations${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  gmailRuntimeStatus: () => request<GmailRuntimeStatus>("/runtime/gmail/status"),
  pollGmailRuntime: () => request<any>("/runtime/gmail/poll", { method: "POST" }),
  registerGmailWatch: () => request<any>("/runtime/gmail/watch/register", { method: "POST" }),
};
