import { useMemo, useState, type ReactNode } from "react";
import {
  DataTable,
  Pagination,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  TableToolbar,
  TableToolbarContent,
  TableToolbarSearch,
} from "@carbon/react";

export type CarbonColumn = { key: string; header: string };
export type CarbonRow = { id: string; [key: string]: string | number };

export function CarbonTable({
  title,
  description,
  headers,
  rows,
  empty = "No rows to show.",
  renderCell,
  toolbar,
  searchPlaceholder = "Search",
  query,
  onQueryChange,
  onRowClick,
  selectedId,
}: {
  title: string;
  description?: string;
  headers: CarbonColumn[];
  rows: CarbonRow[];
  empty?: string;
  renderCell?: (key: string, value: string | number, row: CarbonRow) => ReactNode;
  toolbar?: ReactNode;
  searchPlaceholder?: string;
  query?: string;
  onQueryChange?: (value: string) => void;
  onRowClick?: (id: string) => void;
  selectedId?: string;
}) {
  const [localQuery, setLocalQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const activeQuery = query ?? localQuery;
  const visibleRows = useMemo(() => {
    if (onQueryChange) return rows;
    const needle = activeQuery.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((row) => headers.some((header) => String(row[header.key] ?? "").toLowerCase().includes(needle)));
  }, [rows, headers, activeQuery, onQueryChange]);
  const pageCount = Math.max(1, Math.ceil(visibleRows.length / pageSize) || 1);
  const safePage = Math.min(page, pageCount);

  return (
    <DataTable rows={visibleRows} headers={headers} isSortable size="lg" useZebraStyles>
      {({ rows: tableRows, headers: head, getHeaderProps, getRowProps, getTableProps, getTableContainerProps }) => {
        const pageRows = tableRows.slice((safePage - 1) * pageSize, safePage * pageSize);
        return (
          <TableContainer title={title} description={description} {...getTableContainerProps()}>
            <TableToolbar>
              <TableToolbarContent>
                {toolbar}
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
            <Table {...getTableProps()}>
              <TableHead>
                <TableRow>
                  {head.map((header) => (
                    <TableHeader {...getHeaderProps({ header })}>{header.header}</TableHeader>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {pageRows.length ? (
                  pageRows.map((row) => (
                    <TableRow
                      {...getRowProps({ row, onClick: onRowClick ? () => onRowClick(row.id) : undefined })}
                      className={row.id === selectedId ? "nrm-row-selected" : undefined}
                    >
                      {row.cells.map((cell) => {
                        const key = String(cell.info.header);
                        const value = cell.value as string | number;
                        const source = visibleRows.find((item) => item.id === row.id);
                        return <TableCell key={cell.id}>{renderCell && source ? renderCell(key, value, source) : value}</TableCell>;
                      })}
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={head.length}>{empty}</TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
            {visibleRows.length > 10 ? (
              <Pagination
                page={safePage}
                pageSize={pageSize}
                pageSizes={[10, 25, 50, 100]}
                totalItems={visibleRows.length}
                onChange={({ page: nextPage, pageSize: nextSize }) => {
                  setPage(nextPage);
                  setPageSize(nextSize);
                }}
              />
            ) : null}
          </TableContainer>
        );
      }}
    </DataTable>
  );
}
