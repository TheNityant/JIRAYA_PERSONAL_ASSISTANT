export type Automation = {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  source: "gmail" | "google_calendar" | "github";
  trigger_type: "NEW_EMAIL" | "CALENDAR_CHANGED" | "GITHUB_ISSUE_CHANGED" | "GITHUB_PR_CHANGED" | "GITHUB_WORKFLOW_CHANGED";
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
  status: "AVAILABLE" | "CONNECTED" | "COMING_NEXT" | "SETUP_REQUIRED";
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

export type CalendarEvent = {id: string; summary?: string; htmlLink?: string; recurringEventId?: string; location?: string; description?: string; start?: {dateTime?: string; date?: string}; end?: {dateTime?: string; date?: string}};
export type GithubRepository = {id: number; full_name: string; html_url: string; default_branch: string; private: boolean};
export type GithubItem = {id: number; number?: number; title?: string; name?: string; body?: string; state?: string; status?: string; conclusion?: string; html_url?: string; pull_request?: unknown; updated_at?: string; created_at?: string; head_branch?: string; run_number?: number; user?: {login?: string}; labels?: Array<{name: string}>};

export type ServiceStatus = {
  connected: boolean;
  account: string | null;
  scopes: string[];
  oauth_configured: boolean;
  health: Record<string, unknown>;
  monitor?: { last_status?: string; last_error?: string; last_poll_at?: string; last_decision?: string };
  background_monitor_configured?: boolean;
  poll_seconds?: number;
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
  background_monitor_configured?: boolean;
  poll_seconds: number;
  pubsub_configured: boolean;
  pubsub_topic: string | null;
  web_oauth_configured?: boolean;
  history_cursor: string | null;
  mailbox: string | null;
  last_poll_at?: string | null;
  last_poll_status?: string | null;
  last_poll_decision?: string | null;
  last_error?: string | null;
};

export const API_BASE = process.env.NEXT_PUBLIC_JIRAYA_API_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    credentials: "include",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/") && typeof window !== "undefined") {
      window.dispatchEvent(new Event("jiraya-session-expired"));
    }
    let message = `Request failed with ${response.status}`;
    try { const body = await response.json(); message = typeof body.detail === "string" ? body.detail : "Please check the entered details."; } catch {}
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export type Account = {id: string; email: string; display_name: string};
export type AccountState = {user: Account | null; auth_required: boolean; registration_enabled: boolean};

export const api = {
  account: () => request<AccountState>("/auth/me"),
  login: (email: string, password: string) => request<{user: Account}>("/auth/login", {method: "POST", body: JSON.stringify({email, password})}),
  register: (display_name: string, email: string, password: string) => request<{user: Account}>("/auth/register", {method: "POST", body: JSON.stringify({display_name, email, password})}),
  logout: () => request<{signed_out: boolean}>("/auth/logout", {method: "POST"}),
  startConnection: (provider: "gmail" | "google_calendar" | "github") => {
    const paths = {gmail: "/connections/google/start", google_calendar: "/connections/google/calendar/start", github: "/connections/github/start"};
    return request<{authorization_url: string}>(`${paths[provider]}?response_mode=json`);
  },
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
  calendarConnectUrl: () => `${API_BASE}/connections/google/calendar/start`,
  githubConnectUrl: () => `${API_BASE}/connections/github/start`,
  pollCalendar: () => request<{decision: string}>("/runtime/calendar/poll", { method: "POST" }),
  calendarStatus: () => request<ServiceStatus>("/calendar/status"),
  pollGithub: () => request<{decision: string}>("/runtime/github/poll", {method: "POST"}),
  githubStatus: () => request<ServiceStatus>("/github/status"),
  disconnectCalendar: () =>
    request<{ disconnected: boolean; provider: string }>("/connections/google/calendar/disconnect", {
      method: "POST",
    }),
  disconnectGithub: () =>
    request<{ disconnected: boolean; provider: string }>("/connections/github/disconnect", {
      method: "POST",
    }),
  listCalendarEvents: (limit = 100, timeMin?: string, timeMax?: string) => {
    const params = new URLSearchParams({limit: String(limit)});
    if (timeMin) params.set("time_min", timeMin);
    if (timeMax) params.set("time_max", timeMax);
    return request<{items: CalendarEvent[]; time_min: string; time_max: string; limit_reached: boolean}>(`/calendar/events?${params}`);
  },
  calendarFreeBusy: (input: Record<string, unknown>) =>
    request<any>("/calendar/freebusy", { method: "POST", body: JSON.stringify(input) }),
  createCalendarEvent: (input: Record<string, unknown>) =>
    request<any>("/calendar/events", { method: "POST", body: JSON.stringify(input) }),
  githubProfile: () => request<any>("/github/profile"),
  listGithubRepos: (limit = 30) => request<{items: GithubRepository[]}>(`/github/repos?limit=${limit}`),
  listGithubWorkflowRuns: (owner: string, repo: string, limit = 20) =>
    request<{items: GithubItem[]}>(`/github/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/workflow-runs?limit=${limit}`),
  listGithubIssues: (owner: string, repo: string) => request<{items: GithubItem[]}>(`/github/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/issues?state=all`),
  listGithubPulls: (owner: string, repo: string) => request<{items: GithubItem[]}>(`/github/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/pulls`),
  dispatchGithubWorkflow: (owner: string, repo: string, input: {workflow_id: string; ref: string; inputs: Record<string, string>}) =>
    request<{dispatched: boolean}>(`/github/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/workflow-dispatch`, {method: "POST", body: JSON.stringify(input)}),
  createGithubIssue: (owner: string, repo: string, input: { title: string; body?: string }) =>
    request<any>(`/github/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/issues`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  createGithubBranch: (owner: string, repo: string, input: { new_branch: string; from_ref?: string }) =>
    request<any>(`/github/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/branches`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
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
