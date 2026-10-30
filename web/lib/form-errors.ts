const fieldLabels: Record<string, string> = {new_branch: "New branch name", from_ref: "Source branch", repository: "Repository", workflow_id: "Workflow", action_config: "Action", match_config: "Match", name: "Name"};

export function apiErrorMessage(body: unknown, status: number): string {
  const detail = (body as {detail?: unknown} | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((issue) => {
      if (!issue || typeof issue.msg !== "string") return "";
      const path = Array.isArray(issue.loc) ? issue.loc.filter((part: unknown) => part !== "body" && part !== "query") : [];
      const field = path.map((part: string | number) => fieldLabels[String(part)] || String(part)).join(" · ");
      return `${field ? `${field}: ` : ""}${issue.msg.replace(/^Value error, /, "")}`;
    }).filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  return `Request failed (${status}). Please try again.`;
}
