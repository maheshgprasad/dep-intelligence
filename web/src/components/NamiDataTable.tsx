import { Fragment, useMemo, useState, type ReactNode } from "react";
import {
  DataTable,
  Pagination,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableExpandHeader,
  TableExpandRow,
  TableExpandedRow,
  TableHead,
  TableHeader,
  TableRow,
  TableToolbar,
  TableToolbarContent,
  TableToolbarSearch,
} from "@carbon/react";
import { NamiEmptyState } from "./NamiEmptyState";

export type NamiColumn<T> = {
  key: string;
  header: ReactNode;
  render?: (row: T) => ReactNode;
};

export function NamiDataTable<T extends { id: string }>({
  title,
  description,
  columns,
  rows,
  searchable = false,
  paginate,
  density = "default",
  renderExpanded,
  emptyTitle = "Nothing to show",
  emptyDescription = "This view has no rows yet.",
  emptyAction,
  query,
  onQueryChange,
  selectedId,
  searchPlaceholder = "Search",
}: {
  title?: string;
  description?: string;
  columns: NamiColumn<T>[];
  rows: T[];
  searchable?: boolean;
  paginate?: boolean;
  density?: "default" | "condensed";
  renderExpanded?: (row: T) => ReactNode;
  emptyTitle?: string;
  emptyDescription?: string;
  emptyAction?: { label: string; href?: string; onClick?: () => void };
  query?: string;
  onQueryChange?: (value: string) => void;
  selectedId?: string;
  searchPlaceholder?: string;
}) {
  const [localQuery, setLocalQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const activeQuery = query ?? localQuery;
  const filtered = useMemo(() => {
    if (onQueryChange) return rows;
    const needle = activeQuery.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((row) =>
      columns.some((column) => String((row as Record<string, unknown>)[column.key] ?? "").toLowerCase().includes(needle)),
    );
  }, [rows, columns, activeQuery, onQueryChange]);
  const showPager = (paginate ?? filtered.length > 10) && filtered.length > 10;
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize) || 1);
  const safePage = Math.min(page, pageCount);

  if (rows.length === 0 && !activeQuery.trim()) {
    return <NamiEmptyState title={emptyTitle} description={emptyDescription} primaryAction={emptyAction} />;
  }

  const headers = columns.map((column) => ({ key: column.key, header: typeof column.header === "string" ? column.header : column.key }));
  const tableRows = filtered.map((row) => {
    const record: { id: string; [key: string]: string } = { id: row.id };
    for (const column of columns) record[column.key] = String((row as Record<string, unknown>)[column.key] ?? "");
    return record;
  });

  return (
    <DataTable rows={tableRows} headers={headers} size={density === "condensed" ? "sm" : "lg"} useZebraStyles>
      {({ rows: carbonRows, headers: head, getHeaderProps, getRowProps, getExpandedRowProps, getTableProps, getTableContainerProps }) => {
        const visible = carbonRows.slice((safePage - 1) * pageSize, showPager ? safePage * pageSize : carbonRows.length);
        return (
          <TableContainer title={title} description={description} {...getTableContainerProps()}>
            {searchable ? (
              <TableToolbar>
                <TableToolbarContent>
                  <TableToolbarSearch
                    persistent
                    placeholder={searchPlaceholder}
                    {...(onQueryChange ? { value: activeQuery } : {})}
                    onChange={(event) => {
                      const next = typeof event === "string" ? event : event.target?.value ?? "";
                      if (onQueryChange) onQueryChange(next);
                      else setLocalQuery(next);
                      setPage(1);
                    }}
                  />
                </TableToolbarContent>
              </TableToolbar>
            ) : null}
            {filtered.length === 0 ? (
              <NamiEmptyState title="No matches" description="Try removing a filter or changing your search." />
            ) : (
              <Table {...getTableProps()}>
                <TableHead>
                  <TableRow>
                    {renderExpanded ? <TableExpandHeader /> : null}
                    {head.map((header) => {
                      const column = columns.find((item) => item.key === header.key);
                      const props = getHeaderProps({ header });
                      return (
                        <TableHeader {...props}>
                          {column?.header ?? header.header}
                        </TableHeader>
                      );
                    })}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {visible.map((row) => {
                    const source = filtered.find((item) => item.id === row.id);
                    const selected = selectedId === row.id ? "nami-row-selected" : "";
                    const rowProps = getRowProps({ row });
                    return (
                      <Fragment key={row.id}>
                        {renderExpanded ? (
                          <TableExpandRow {...rowProps} className={`${rowProps.className ?? ""} ${selected}`.trim()}>
                            {row.cells.map((cell) => (
                              <TableCell key={cell.id}>{source ? renderCell(columns, source, cell.info.header) : cell.value}</TableCell>
                            ))}
                          </TableExpandRow>
                        ) : (
                          <TableRow {...rowProps} className={`${rowProps.className ?? ""} ${selected}`.trim()}>
                            {row.cells.map((cell) => (
                              <TableCell key={cell.id}>{source ? renderCell(columns, source, cell.info.header) : cell.value}</TableCell>
                            ))}
                          </TableRow>
                        )}
                        {renderExpanded && source && row.isExpanded ? (
                          <TableExpandedRow colSpan={columns.length + 1} {...getExpandedRowProps({ row })}>
                            {renderExpanded(source)}
                          </TableExpandedRow>
                        ) : null}
                      </Fragment>
                    );
                  })}
                </TableBody>
              </Table>
            )}
            {showPager ? (
              <Pagination
                page={safePage}
                pageSize={pageSize}
                pageSizes={[10, 25, 50, 100]}
                totalItems={filtered.length}
                onChange={({ page: next, pageSize: size }) => {
                  setPage(next);
                  setPageSize(size);
                }}
              />
            ) : null}
          </TableContainer>
        );
      }}
    </DataTable>
  );
}

function renderCell<T extends { id: string }>(columns: NamiColumn<T>[], row: T, key: string): ReactNode {
  const column = columns.find((item) => item.key === key);
  if (column?.render) return column.render(row);
  return String((row as Record<string, unknown>)[key] ?? "");
}
