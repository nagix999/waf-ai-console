// A plain-text parser for the subset used by backend/app/data/production_api.md.
// It never evaluates HTML, URLs, code, or inline Markdown. Render text as React
// children; these blocks must not be passed to dangerouslySetInnerHTML.

const horizontalSpace = character => character === " " || character === "\t";
const hasLineEnding = text => /[\r\n\u2028\u2029]/.test(text);

function lineStart(line) {
  let index = 0;
  while (line[index] === " ") index += 1;
  return index <= 3 ? index : -1;
}

function heading(line) {
  let index = lineStart(line);
  if (index < 0) return null;
  const start = index;
  while (line[index] === "#") index += 1;
  const level = index - start;
  if (level < 1 || level > 6 || !horizontalSpace(line[index])) return null;
  const spaceStart = index;
  while (horizontalSpace(line[index])) index += 1;
  let end = line.length;
  // Preserve the old parser's single retained character for a blank heading.
  if (index === end && index - spaceStart > 1) index -= 1;
  if (index >= end) return null;
  while (end > index + 1 && horizontalSpace(line[end - 1])) end -= 1;
  const text = line.slice(index, end);
  return hasLineEnding(text) ? null : { level, text };
}

function fence(line) {
  const start = lineStart(line);
  if (start < 0 || !["`", "~"].includes(line[start])) return null;
  let index = start;
  while (line[index] === line[start]) index += 1;
  if (index - start < 3) return null;
  const suffix = line.slice(index);
  if (hasLineEnding(suffix) || (line[start] === "`" && suffix.includes("`"))) return null;
  return { marker: line.slice(start, index), language: suffix.trim().split(/\s+/)[0] || "" };
}

function closesFence(line, marker) {
  const start = lineStart(line);
  if (start < 0 || line[start] !== marker[0]) return false;
  let index = start;
  while (line[index] === marker[0]) index += 1;
  if (index - start < marker.length) return false;
  const end = line.length;
  while (index < end && horizontalSpace(line[index])) index += 1;
  return index === end;
}

function listItem(line) {
  let index = lineStart(line);
  if (index < 0) return null;
  const start = index;
  const ordered = line[index] >= "0" && line[index] <= "9";
  if (ordered) {
    while (line[index] >= "0" && line[index] <= "9") index += 1;
    if (![".", ")"].includes(line[index])) return null;
  } else if (!["-", "+", "*"].includes(line[index])) return null;
  index += 1;
  if (!horizontalSpace(line[index])) return null;
  const numberEnd = index - 1;
  while (horizontalSpace(line[index])) index += 1;
  const text = line.slice(index);
  if (hasLineEnding(text)) return null;
  return { ordered, start: ordered ? Number.parseInt(line.slice(start, numberEnd), 10) : 1, text };
}

function lastTickRuns(line) {
  // A suffix lookup for every unmatched width can become quadratic. The last
  // run of each width answers the same question after one linear pre-scan.
  const last = new Map();
  let index = 0;
  while (index < line.length) {
    if (line[index] !== "`") { index += 1; continue; }
    let end = index + 1;
    while (line[end] === "`") end += 1;
    last.set(end - index, index);
    index = end;
  }
  return last;
}

function tableRow(line) {
  const source = line.trim();
  const cells = [];
  let cell = "";
  let ticks = 0;
  let separators = 0;
  const closingTicks = lastTickRuns(source);
  let index = 0;
  while (index < source.length) {
    const character = source[index];
    if (!ticks && character === "\\" && index + 1 < source.length) {
      cell += source.slice(index, index + 2);
      index += 2;
    } else if (character === "`") {
      let end = index + 1;
      while (source[end] === "`") end += 1;
      const width = end - index;
      if (ticks === width) ticks = 0;
      else if (!ticks && closingTicks.get(width) >= end) ticks = width;
      cell += source.slice(index, end);
      index = end;
    } else if (character === "|" && !ticks) {
      cells.push(cell.trim());
      cell = "";
      separators += 1;
      index += 1;
    } else {
      cell += character;
      index += 1;
    }
  }
  cells.push(cell.trim());
  if (separators && cells[0] === "" && source.startsWith("|")) cells.shift();
  if (separators && cells[cells.length - 1] === "" && source.endsWith("|")) cells.pop();
  return { cells, separators };
}

function startsTable(lines, index) {
  if (index + 1 >= lines.length) return false;
  const first = tableRow(lines[index].text);
  const delimiter = tableRow(lines[index + 1].text);
  return first.separators > 0 && first.cells.length > 0
    && first.cells.length === delimiter.cells.length
    && delimiter.cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function startsBlock(lines, index) {
  const line = lines[index].text;
  return !line.trim() || heading(line) || fence(line) || listItem(line) || startsTable(lines, index);
}

export function parseApiDocument(markdown) {
  const source = typeof markdown === "string" ? markdown : "";
  const split = source.split("\n");
  const lines = split.map((line, index) => ({
    text: line.endsWith("\r") ? line.slice(0, -1) : line,
    raw: line + (index < split.length - 1 ? "\n" : ""),
  }));
  const document = { title: "", intro: [], sections: [] };
  let blocks = document.intro;
  let hasTitle = false;
  let index = 0;
  while (index < lines.length) {
    const line = lines[index].text;
    if (!line.trim()) { index += 1; continue; }

    const code = fence(line);
    if (code) {
      index += 1;
      let text = "";
      while (index < lines.length && !closesFence(lines[index].text, code.marker)) {
        text += lines[index].raw;
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push({ type: "code", language: code.language, text });
      continue;
    }

    const title = heading(line);
    if (title) {
      if (title.level === 1 && !hasTitle) {
        document.title = title.text;
        hasTitle = true;
      } else if (title.level === 2) {
        const section = { id: `api-section-${document.sections.length}`, title: title.text, blocks: [] };
        document.sections.push(section);
        blocks = section.blocks;
      } else {
        blocks.push({ type: "heading", level: title.level, text: title.text });
      }
      index += 1;
      continue;
    }

    if (startsTable(lines, index)) {
      const headers = tableRow(line).cells;
      const rows = [];
      index += 2;
      while (index < lines.length) {
        const next = lines[index].text;
        if (!next.trim() || heading(next) || fence(next) || listItem(next)) break;
        const row = tableRow(next);
        if (!row.separators) break;
        rows.push(row.cells);
        index += 1;
      }
      blocks.push({ type: "table", headers, rows });
      continue;
    }

    const item = listItem(line);
    if (item) {
      const items = [];
      while (index < lines.length) {
        const next = listItem(lines[index].text);
        if (!next || next.ordered !== item.ordered) break;
        items.push(next.text);
        index += 1;
      }
      blocks.push({ type: "list", ordered: item.ordered, start: item.start, items });
      continue;
    }

    const paragraph = [line];
    index += 1;
    while (index < lines.length && !startsBlock(lines, index)) {
      paragraph.push(lines[index].text);
      index += 1;
    }
    blocks.push({ type: "paragraph", text: paragraph.join("\n") });
  }
  return document;
}

function blockText(block) {
  if (block.type === "list") return block.items.join("\n");
  if (block.type === "table") return [...block.headers, ...block.rows.flat()].join("\n");
  return block.text || "";
}

export function filterApiSections(sections, query) {
  const needle = typeof query === "string" ? query.trim().toLowerCase() : "";
  if (!needle) return sections;
  return sections.filter((section) => (
    [section.title, ...section.blocks.map(blockText)].join("\n").toLowerCase().includes(needle)
  ));
}

export function apiSectionNeighbors(sections, selectedId, query = "") {
  const entries = query.trim() ? sections : [{ id: "intro", title: "정의서 안내" }, ...sections];
  const index = entries.findIndex(section => section.id === selectedId);
  if (index < 0) return { previous: null, next: null };
  return { previous: entries[index - 1] || null, next: entries[index + 1] || null };
}
