export function branchNameError(value: string): string | null {
  if (!value) return "Enter a branch name.";
  if (/\s/.test(value)) return "Branch names cannot contain spaces. Use hyphens, for example feature/follow-up.";
  if (value.length > 240) return "Keep the branch name to 240 characters or fewer.";
  if (value === "@" || value.startsWith("-") || value.includes("..") || value.includes("@{") || /[\x00-\x20\x7f~^:?*\[\\]/.test(value) || value.endsWith(".") || value.split("/").some((part) => !part || part.startsWith(".") || part.endsWith(".lock"))) return "Use a valid Git branch name, for example feature/follow-up.";
  return null;
}
export function repositoryError(value: string): string | null {
  return /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(value) && !value.split("/").some((part) => part === "." || part === "..") ? null : "Choose a repository in owner/name form, for example TheNityant/JIRAYA_PERSONAL_ASSISTANT.";
}
