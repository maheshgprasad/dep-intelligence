import type { ReactNode } from "react";
import { Breadcrumb, BreadcrumbItem } from "@carbon/react";

export function PageHeader({
  title,
  description,
  eyebrow,
  actions,
  breadcrumbs,
  status,
}: {
  title: string;
  description?: string;
  eyebrow?: string;
  actions?: ReactNode;
  breadcrumbs?: { label: string; href?: string }[];
  status?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div>
        {breadcrumbs && breadcrumbs.length > 0 ? (
          <Breadcrumb noTrailingSlash>
            {breadcrumbs.map((item) => (
              <BreadcrumbItem key={item.label} href={item.href}>
                {item.label}
              </BreadcrumbItem>
            ))}
          </Breadcrumb>
        ) : null}
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
        {status}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}
