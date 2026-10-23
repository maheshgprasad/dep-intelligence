import type { ReactNode } from "react";
import { Button, Link, Tile } from "@carbon/react";

export function NamiEmptyState({
  title,
  description,
  primaryAction,
  secondaryAction,
  icon,
}: {
  title: string;
  description: string;
  primaryAction?: { label: string; onClick?: () => void; href?: string };
  secondaryAction?: { label: string; href: string };
  icon?: ReactNode;
}) {
  return (
    <Tile className="empty-state">
      {icon}
      <h2>{title}</h2>
      <p>{description}</p>
      <div className="empty-actions">
        {primaryAction?.href ? (
          <Button href={primaryAction.href} kind="primary">
            {primaryAction.label}
          </Button>
        ) : null}
        {primaryAction?.onClick ? (
          <Button kind="primary" onClick={primaryAction.onClick}>
            {primaryAction.label}
          </Button>
        ) : null}
        {secondaryAction ? <Link href={secondaryAction.href}>{secondaryAction.label}</Link> : null}
      </div>
    </Tile>
  );
}
