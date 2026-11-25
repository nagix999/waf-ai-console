import "./dataTable.css";

const noSorting = [];
const rowId = row => row.id;

// Documents, reports and matrices deliberately remain semantic HTML tables.
export function Table({ className = "", children, ...props }) {
  return <table {...props} className={`console-table ${className}`}>{children}</table>;
}

// Server rendering keeps a complete, readable table rather than an empty
// imperative widget. Browser lists use Tabulator through DataTable.jsx.
export default function NativeDataTable({ data, columns, getRowId = rowId, rowClassName, onRowClick,
  sorting = noSorting, onSortingChange, label, headerGroups, className = "", empty = "표시할 항목이 없습니다." }) {
  return <div className="table-wrap data-table-wrap" role="region" aria-label={label} tabIndex={0}>
    <Table className={`data-table ${className}`}>
      {label && <caption className="sr-only">{label}</caption>}
      <colgroup>{columns.map(column => <col key={column.id} style={column.width ? { width: column.width } : undefined} />)}</colgroup>
      <thead>{headerGroups && <tr className="r3-group-header">{headerGroups.map((group, index) => <th key={index} scope="colgroup" colSpan={group.span}>{group.label}</th>)}</tr>}<tr>{columns.map(column => {
        const sorted = sorting.find(sort => sort.id === column.id);
        return <th key={column.id} scope="col" className={column.className}
          aria-sort={sorted ? sorted.desc ? "descending" : "ascending" : undefined}>
          {column.sortable && onSortingChange ? <button type="button" className="table-sort" onClick={() => onSortingChange([{ id: column.id, desc: sorted ? !sorted.desc : false }])}>
            {column.header}<span aria-hidden="true">{sorted ? sorted.desc ? "↓" : "↑" : "↕"}</span>
          </button> : column.header}
        </th>;
      })}</tr></thead>
      <tbody>{data.map((row, index) => <tr key={`${getRowId(row) ?? "row"}:${index}`} className={rowClassName?.(row)} onClick={onRowClick ? event => { if (!event.target.closest("button,a,input,select,textarea,[role=button]")) onRowClick(row); } : undefined} style={onRowClick ? { cursor: "pointer" } : undefined}>
        {columns.map(column => <td key={column.id} className={column.className}>
          {column.render ? column.render(row, index) : String(row[column.id] ?? "—")}
        </td>)}
      </tr>)}{!data.length && <tr><td className="empty" colSpan={columns.length}>{empty}</td></tr>}</tbody>
    </Table>
  </div>;
}
