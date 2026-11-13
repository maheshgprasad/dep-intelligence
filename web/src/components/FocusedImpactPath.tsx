import { relativePath, symbolName } from "../labels";

export type PathHop = {
  relation?: string;
  source?: string;
  target?: string;
  source_name?: string;
  target_name?: string;
  source_file?: string;
  target_file?: string;
};

const RELATIONS: Record<string, string> = {
  CALLS: "calls",
  IMPORTS_FROM: "imports from",
  HANDLED_BY: "handled by",
  IMPLEMENTED_BY: "implemented by",
  CONSUMES_HTTP: "consumes HTTP endpoint",
  CONSUMES_GRPC: "consumes gRPC operation",
  PRODUCES_EVENT: "produces event",
  CONSUMED_BY: "consumed by",
  TESTED_BY: "tested by",
};

export function FocusedImpactPath({ steps }: { steps: PathHop[] }) {
  if (!steps.length) return <p className="helper">No structural path was recorded for this candidate.</p>;
  const nodes = [display(steps[0].source_name, steps[0].source)];
  const files = [fileLabel(steps[0].source_file)];
  const services = [serviceOf(steps[0].source)];
  const relations: string[] = [];
  for (const step of steps) {
    relations.push(RELATIONS[step.relation || ""] || (step.relation || "").replaceAll("_", " ").toLowerCase());
    nodes.push(display(step.target_name, step.target));
    files.push(fileLabel(step.target_file));
    services.push(serviceOf(step.target));
  }
  return (
    <ol className="impact-path">
      {nodes.map((node, index) => (
        <li key={`${node}-${index}`}>
          {index > 0 && services[index] && services[index] !== services[index - 1] ? <p className="boundary">repository boundary</p> : null}
          {services[index] && services[index] !== services[index - 1] ? <p className="path-service">{services[index]}</p> : null}
          <p className="path-symbol">{node || "Affected code"}</p>
          {files[index] ? <p className="helper">{files[index]}</p> : null}
          {relations[index] ? <p className="path-relation">{relations[index]}</p> : null}
        </li>
      ))}
    </ol>
  );
}

function display(name?: string, key?: string): string {
  return present(name) || present(key);
}

function present(value?: string): string {
  const text = (value || "").trim();
  if (!text || /^[0-9a-f]{64}$/i.test(text)) return "";
  if (/^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b/i.test(text) && text.includes("/")) return text;
  if (text.startsWith("<inline ")) return text;
  const declared = text.match(/^http:[^:]*:([A-Za-z]+):(\/.*)$/);
  if (declared) return `${declared[1].toUpperCase()} ${declared[2]}`;
  return symbolName(text);
}

function fileLabel(value?: string): string {
  const file = relativePath(value || "");
  return file && !/^[0-9a-f]{64}$/i.test(file) ? file : "";
}

function serviceOf(key?: string): string {
  if (!key || !key.includes(":")) return "";
  const head = key.split(":")[0];
  return head.includes("/") ? "" : head;
}
