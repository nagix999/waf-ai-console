// Constant-size page navigation, even when a server reports millions of rows.
export function paginationState(total, limit, offset) {
  total = Number.isSafeInteger(total) && total > 0 ? total : 0;
  limit = Number.isSafeInteger(limit) && limit > 0 ? limit : 25;
  offset = Number.isSafeInteger(offset) && offset > 0 ? offset : 0;
  const pageCount = Math.ceil(total / limit);
  const page = pageCount ? Math.min(pageCount, Math.floor(offset / limit) + 1) : 0;
  const start = Math.max(1, Math.min(page - 2, pageCount - 4));
  const end = Math.min(pageCount, start + 4);
  const pages = Array.from({ length: Math.max(0, end - start + 1) }, (_, i) => start + i);
  return { total, limit, offset, page, pageCount, pages,
    lastOffset: Math.max(0, (pageCount - 1) * limit),
    firstRow: total && offset < total ? offset + 1 : 0,
    lastRow: total && offset < total ? Math.min(offset + limit, total) : 0 };
}
