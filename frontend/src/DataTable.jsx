import NativeDataTable from "./NativeDataTable.jsx";
import TabulatorTable from "./TabulatorTable.jsx";
export { Table } from "./NativeDataTable.jsx";

// The API owns ordering and pagination. Both renderers preserve the supplied
// page and expose the same controlled sorting and React cell callbacks.
export default function DataTable(props) {
  return typeof document === "undefined" ? <NativeDataTable {...props} /> : <TabulatorTable {...props} />;
}

export function serverSorting(sortBy, sortOrder) { return [{ id: sortBy, desc: sortOrder === "desc" }]; }
export function changedSort(update, current) {
  const [next] = (typeof update === "function" ? update(current) : update) || [];
  return next ? { sort_by: next.id, sort_order: next.desc ? "desc" : "asc", offset: 0 } : {};
}
