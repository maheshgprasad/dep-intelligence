const HIDDEN_KEYS = new Set([
  "file",
  "file_path",
  "path",
  "qualified_name",
  "checkout",
  "source",
  "target",
  "source_name",
  "target_name",
  "old_path",
  "new_path",
  "path_files",
  "queryTarget",
]);

export function symbolName(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "";
  const qualified = trimmed.split("::").pop() || trimmed;
  const base = qualified.split(/[/\\]/).pop() || qualified;
  return base;
}

export function isFilePath(value: string): boolean {
  if (!value.includes("/") && !value.includes("\\")) return false;
  if (/^(GET|POST|PUT|PATCH|DELETE|HEAD)\s+\//.test(value)) return false;
  if (/^\/api(?:\/|$)/.test(value) || /^\/v\d+(?:\/|$)/.test(value)) return false;
  return true;
}

export function repoSymbol(repository: string, symbol: string): string {
  const repo = repository.trim();
  const name = isFilePath(symbol) || symbol.includes("/") ? symbolName(symbol) : symbol.trim();
  if (repo && name && repo !== name) return `${repo} · ${name}`;
  return name || repo;
}

export function hideFilePaths(text: string): string {
  return text.replace(/(?:[A-Za-z]:)?(?:\/|\\)(?:[\w.+@=-]+(?:\/|\\))+[\w.+@=-]+/g, (match) => {
    if (/^\/api(?:\/|$)/.test(match) || /^\/v\d+(?:\/|$)/.test(match)) return match;
    return symbolName(match);
  });
}

export function relativePath(value: string): string {
  const trimmed = value.trim();
  if (!trimmed || trimmed.startsWith("/") || /^[A-Za-z]:[\\/]/.test(trimmed)) return "";
  if (trimmed.split("/").includes("..")) return "";
  return trimmed;
}

export function graphQueryTarget(row: Record<string, unknown>): string {
  return String(row.qualified_name || row.key || row.name || "");
}

export function visibleFields(row: Record<string, unknown>, repository: string): Record<string, string> {
  const rawSymbol = String(row.name || row.qualified_name || row.file || row.file_path || "");
  const repo = String(row.service || row.service_id || row.repository || repository);
  const fields: Record<string, string> = {};
  if (repo) fields.repository = repo;
  if (rawSymbol) fields.symbol = symbolName(rawSymbol);
  const file = relativePath(String(row.file_path || row.file || row.path || ""));
  if (file) fields.file = file;
  const qualified = String(row.qualified_name || "");
  if (qualified) fields.qualified_name = qualified;
  const key = String(row.key || "");
  if (key) fields.graph_key = key;
  const line = row.line_start ?? row.line;
  if (line) fields.line = String(line);
  for (const [entry, value] of Object.entries(row)) {
    if (HIDDEN_KEYS.has(entry) || entry === "name" || entry === "id" || entry === "key" || entry === "line_start") continue;
    const text = typeof value === "string" ? value : JSON.stringify(value);
    if (isFilePath(text) || text.startsWith("/")) continue;
    fields[entry] = hideFilePaths(text);
  }
  return fields;
}
