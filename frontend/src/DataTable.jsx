import { useMemo } from "react";
import { useTable, tableFeatures, rowSortingFeature } from "@tanstack/react-table";
import "./dataTable.css";

const features = tableFeatures({ rowSortingFeature });
const noSorting = [];
const rowId = row => row.id;

// Documents and fixed-order matrices share the same visual foundation without
// adding list controls or changing their row/column semantics.
export function Table({ className = "", children, ...props }) {
  return <table {...props} className={`console-table ${className}`}>{children}</table>;
}

// Data is already filtered/paged by the API. Sorting is controlled by the
// caller and must trigger a server query; never reorder just the loaded page.
// Column renderers are plain render callbacks (no hooks), not component types.
export default function DataTable({ data, columns, getRowId = rowId, rowClassName,
  sorting = noSorting, onSortingChange, label, className = "", empty = "표시할 항목이 없습니다." }) {
  const definitions = useMemo(() => columns.map(column => ({
    id: column.id, accessorFn: row => row[column.id], header: column.header,
    enableSorting: Boolean(onSortingChange && column.sortable), meta: column,
  })), [columns, onSortingChange]);
  const table = useTable({ features, data, columns: definitions, getRowId,
    manualSorting: true, enableMultiSort: false, enableSortingRemoval: false,
    state: { sorting }, onSortingChange });
  return <div className="table-wrap data-table-wrap" role="region" aria-label={label} tabIndex={0}>
    <Table className={`data-table ${className}`}>
      {label && <caption className="sr-only">{label}</caption>}
      <colgroup>{columns.map(column => <col key={column.id} style={column.width ? { width: column.width } : undefined} />)}</colgroup>
      <thead>{table.getHeaderGroups().map(group => <tr key={group.id}>{group.headers.map(header => {
        const sorted = header.column.getIsSorted();
        return <th key={header.id} scope="col" className={header.column.columnDef.meta.className}
          aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined}>
          {header.column.getCanSort() ? <button type="button" className="table-sort" onClick={header.column.getToggleSortingHandler()}>
            {header.column.columnDef.header}<span aria-hidden="true">{sorted === "asc" ? "↑" : sorted === "desc" ? "↓" : "↕"}</span>
          </button> : header.column.columnDef.header}
        </th>;
      })}</tr>)}</thead>
      <tbody>{table.getRowModel().rows.map(row => <tr key={row.id} className={rowClassName?.(row.original)}>
        {row.getAllCells().map(cell => <td key={cell.id} className={cell.column.columnDef.meta.className}>
          {cell.column.columnDef.meta.render ? cell.column.columnDef.meta.render(row.original, row.index) : String(cell.getValue() ?? "—")}
        </td>)}
      </tr>)}{!data.length && <tr><td className="empty" colSpan={columns.length}>{empty}</td></tr>}</tbody>
    </Table>
  </div>;
}

export function serverSorting(sortBy, sortOrder) { return [{ id: sortBy, desc: sortOrder === "desc" }]; }
export function changedSort(update, current) {
  const [next] = (typeof update === "function" ? update(current) : update) || [];
  return next ? { sort_by: next.id, sort_order: next.desc ? "desc" : "asc", offset: 0 } : {};
}
