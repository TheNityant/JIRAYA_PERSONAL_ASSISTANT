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

const API_BASE = process.env.NEXT_PUBLIC_JIRAYA_API_URL ?? "http://127.0.0.1:8000";

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
};
