import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { TabulatorFull as Tabulator } from "tabulator-tables";
import NativeDataTable from "./NativeDataTable.jsx";
import { useConsolePreferences } from "./consolePreferences.jsx";
import "./tabulatorTable.css";

const defaultRowId = row => row.id;
const interactive = "button,a,input,select,textarea,[role=button]";
const noSorting = [];

export function nextServerSorting(sorting, id) {
  const current = sorting.find(sort => sort.id === id);
  return [{ id, desc: current ? !current.desc : false }];
}

export function tableColumnMinWidth(column) {
  if (Number.isFinite(column.minWidth) && column.minWidth >= 40) return column.minWidth;
  const fixed = column.width && !String(column.width).includes("%") ? Number.parseFloat(column.width) : null;
  if (fixed && fixed <= 120) return Math.max(40, fixed);
  if (column.className?.split(/\s+/).includes("numeric")) return 88;
  if (/summary|explanation|description|comment/.test(column.id)) return 230;
  if (/reference/.test(column.id)) return 160;
  if (/^(case_name|name|signature)$/.test(column.id)) return 170;
  return 120;
}

// Never put fields on the caller's objects. The internal key also supports
// summaries whose rows have no public id, or happen to share the same value.
export function tablePageRows(data, getRowId = defaultRowId) {
  const seen = new Map();
  return data.map((row, index) => {
    const value = getRowId(row);
    const id = value === undefined || value === null ? `index:${index}` : `id:${String(value)}`;
    const occurrence = seen.get(id) || 0;
    seen.set(id, occurrence + 1);
    return { __consoleRow: row, __consoleRowIndex: index, __consoleRowKey: `${id}:${occurrence}` };
  });
}

// The API owns filtering, ordering and pagination. Tabulator only renders the
// supplied page. React portals preserve context and render user text safely.
export default function TabulatorTable({ data = [], columns = [], getRowId = defaultRowId, rowClassName, onRowClick,
  sorting = noSorting, onSortingChange, headerGroups, label, className = "", empty = "표시할 항목이 없습니다." }) {
  const { locale } = useConsolePreferences();
  const helpId = useId();
  const element = useRef(null), instance = useRef(null), latest = useRef(null);
  const [regions, setRegions] = useState([]);
  const [renderFailed, setRenderFailed] = useState(false);
  latest.current = { data, columns, getRowId, rowClassName, onRowClick, empty, sorting, onSortingChange, headerGroups };
  const structure = JSON.stringify({
    columns: columns.map(column => [column.id, column.width, column.minWidth, column.className, Boolean(column.sortable && onSortingChange)]),
    groups: headerGroups?.map(group => group.span),
  });

  useEffect(() => {
    if (!element.current || renderFailed) return;
    const mount = { alive: true, built: false, queued: false, structure, regions: new Map(), table: null, classes: new WeakMap() };
    const placeholder = document.createElement("div");
    placeholder.className = "console-tabulator-empty";
    mount.regions.set("empty", { key: "empty", kind: "empty", element: placeholder });
    function publish() {
      if (mount.queued || !mount.alive) return;
      mount.queued = true;
      queueMicrotask(() => {
        mount.queued = false;
        if (mount.alive) {
          const next = [...mount.regions.values()].filter(region => region.element.isConnected);
          setRegions(previous => previous.length === next.length && previous.every((region, index) => region.key === next[index].key && region.element === next[index].element) ? previous : next);
        }
      });
    }
    function updateRow(row) {
      const node = row.getElement();
      const old = mount.classes.get(node) || [];
      if (old.length) node.classList.remove(...old);
      const next = latest.current.rowClassName?.(row.getData().__consoleRow)?.split(/\s+/).filter(Boolean) || [];
      if (next.length) node.classList.add(...next);
      mount.classes.set(node, next);
      node.tabIndex = latest.current.onRowClick ? 0 : -1;
    }
    function updateSort() {
      if (!mount.built) return;
      for (const column of latest.current.columns) {
        // Tabulator returns false (not null) for a column it cannot find.
        const component = mount.table.getColumn(column.id);
        const node = component ? component.getElement() : null;
        const sort = latest.current.sorting.find(sort => sort.id === column.id);
        if (sort) node?.setAttribute("aria-sort", sort.desc ? "descending" : "ascending");
        else node?.removeAttribute("aria-sort");
      }
    }
    function populate() {
      if (!mount.built || !mount.alive) return;
      for (const [key, region] of mount.regions) if (region.kind === "cell") mount.regions.delete(key);
      mount.table.replaceData(tablePageRows(latest.current.data, latest.current.getRowId)).then(() => {
        if (mount.alive) { updateSort(); publish(); }
      }).catch(() => { if (mount.alive) setRenderFailed(true); });
    }
    function titleHost(key, region) {
      const host = document.createElement("div");
      host.className = "console-tabulator-header-content";
      mount.regions.set(key, { key, element: host, ...region });
      publish();
      return host;
    }
    const leaves = latest.current.columns.map(column => ({
      field: column.id, title: "", width: column.width, minWidth: tableColumnMinWidth(column),
      headerSort: false, cssClass: column.className || "", variableHeight: true,
      titleFormatter: () => titleHost(`header:${column.id}`, { kind: "header", field: column.id }),
      formatter: cell => {
        const item = cell.getRow().getData();
        const host = document.createElement("div");
        host.className = "console-tabulator-cell-content";
        const key = `cell:${item.__consoleRowKey}:${column.id}`;
        mount.regions.set(key, { key, kind: "cell", element: host, index: item.__consoleRowIndex, field: column.id });
        publish();
        return host;
      },
    }));
    let columnOffset = 0;
    const definitions = latest.current.headerGroups?.length ? latest.current.headerGroups.map((group, index) => {
      const children = leaves.slice(columnOffset, columnOffset + group.span);
      columnOffset += group.span;
      return { title: "", columns: children, titleFormatter: () => titleHost(`group:${index}`, { kind: "group", index }) };
    }) : leaves;
    // Retain any trailing column rather than silently dropping it if an older
    // page's grouped-header spans don't cover an added action column.
    if (latest.current.headerGroups?.length && columnOffset < leaves.length) definitions.push(...leaves.slice(columnOffset));
    const table = new Tabulator(element.current, {
      data: [], columns: definitions, index: "__consoleRowKey", layout: "fitColumns", renderVertical: "basic", renderHorizontal: "basic",
      columnDefaults: { headerSort: false, resizable: true },
      placeholder, selectableRows: false, movableColumns: false, clipboard: false,
      rowFormatter: updateRow,
    });
    mount.table = table; mount.populate = populate; mount.placeholder = placeholder;
    mount.refresh = () => { if (mount.built) { mount.table.getRows().forEach(updateRow); updateSort(); } };
    instance.current = mount;
    table.on("tableBuilt", () => { mount.built = true; populate(); });
    table.on("renderComplete", publish);
    table.on("rowClick", (event, row) => {
      if (!event.target.closest(interactive)) latest.current.onRowClick?.(row.getData().__consoleRow);
    });
    return () => {
      mount.alive = false;
      mount.regions.clear();
      instance.current = null;
      table.destroy();
    };
  }, [structure, renderFailed]);

  useEffect(() => { instance.current?.populate(); }, [data, structure]);
  useLayoutEffect(() => {
    // Layout effects run before the effect that replaces a changed table.
    // Never apply new columns to the previous instance during that commit.
    const mount = instance.current;
    if (mount?.structure === structure) mount.refresh();
  }, [structure, sorting, columns, rowClassName, onRowClick]);
  useLayoutEffect(() => {
    const mount = instance.current;
    if (!mount?.alive || !mount.built || mount.structure !== structure) return;
    // Tabulator measures the empty portal hosts during construction. Reflow
    // after React fills rich headers, but only when their real heights change:
    // an unconditional redraw would create a renderComplete feedback loop.
    mount.table.getRows().forEach(row => row.normalizeHeight());
    const heights = regions.filter(region => region.kind === "header" || region.kind === "group").map(region => `${region.key}:${region.element.scrollHeight}`).join("|");
    if (mount.headerHeights !== heights) {
      mount.headerHeights = heights;
      mount.table.redraw();
    }
  }, [structure, regions, columns]);

  function keyDown(event) {
    if (event.target.closest(interactive)) return;
    const mount = instance.current;
    const rowNode = event.target.closest(".tabulator-row");
    if (!mount?.built || !rowNode) return;
    const rows = mount.table.getRows();
    const index = rows.findIndex(row => row.getElement() === rowNode);
    if (index < 0) return;
    if (["Enter", " "].includes(event.key) && onRowClick) {
      event.preventDefault(); onRowClick(rows[index].getData().__consoleRow); return;
    }
    const target = event.key === "ArrowDown" ? index + 1 : event.key === "ArrowUp" ? index - 1 : event.key === "Home" ? 0 : event.key === "End" ? rows.length - 1 : null;
    if (target !== null) { event.preventDefault(); rows[Math.max(0, Math.min(rows.length - 1, target))]?.getElement().focus(); }
  }

  if (typeof document === "undefined" || renderFailed) return <NativeDataTable data={data} columns={columns} getRowId={getRowId} rowClassName={rowClassName} onRowClick={onRowClick} sorting={sorting} onSortingChange={onSortingChange} headerGroups={headerGroups} label={label} className={className} empty={empty} />;
  return <div className={`table-wrap data-table-wrap console-tabulator-wrap ${className}`} role="region" aria-label={label} aria-describedby={onRowClick ? helpId : undefined} tabIndex={0} onKeyDown={keyDown}>
    {onRowClick && <p id={helpId} className="sr-only">{locale === "en" ? "Use the up and down arrows to move between rows. Press Enter or Space to open details. Buttons are also available in each row." : "위아래 화살표로 행을 이동하고 Enter 또는 Space로 상세를 엽니다. 각 행의 상세 버튼도 사용할 수 있습니다."}</p>}
    <div ref={element} className={`console-tabulator data-table ${className}`} aria-label={label} />
    {regions.map(region => {
      if (region.kind === "empty") return createPortal(empty, region.element, region.key);
      if (region.kind === "group") return createPortal(headerGroups?.[region.index]?.label || null, region.element, region.key);
      const column = columns.find(column => column.id === region.field);
      if (!column) return null;
      if (region.kind === "header") {
        const sorted = sorting.find(sort => sort.id === column.id);
        const content = column.sortable && onSortingChange ? <button type="button" className="table-sort" onClick={() => onSortingChange(nextServerSorting(sorting, column.id))}>{column.header}<span aria-hidden="true">{sorted ? sorted.desc ? "↓" : "↑" : "↕"}</span></button> : column.header;
        return createPortal(content, region.element, region.key);
      }
      const row = data[region.index];
      return row ? createPortal(column.render ? column.render(row, region.index) : String(row[column.id] ?? "—"), region.element, region.key) : null;
    })}
  </div>;
}
