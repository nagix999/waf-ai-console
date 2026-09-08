// A plain-text parser for the subset used by docs/Production_API_v0.1.md.
// It never evaluates HTML, URLs, code, or inline Markdown. Render text as React
// children; these blocks must not be passed to dangerouslySetInnerHTML.

function heading(line) {
  const match = /^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*$/.exec(line);
  return match ? { level: match[1].length, text: match[2] } : null;
}

function fence(line) {
  const match = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
  if (!match || (match[1][0] === "`" && match[2].includes("`"))) return null;
  return { marker: match[1], language: match[2].trim().split(/\s+/)[0] || "" };
}

function closesFence(line, marker) {
  const match = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(line);
  return Boolean(match && match[1][0] === marker[0] && match[1].length >= marker.length);
}

function listItem(line) {
  const match = /^ {0,3}([-+*]|\d+[.)])[ \t]+(.*)$/.exec(line);
  if (!match) return null;
  const ordered = /^\d/.test(match[1]);
  return { ordered, start: ordered ? Number.parseInt(match[1], 10) : 1, text: match[2] };
}

function hasClosingTicks(line, from, width) {
  for (let index = from; index < line.length; index += 1) {
    if (line[index] !== "`") continue;
    let end = index + 1;
    while (line[end] === "`") end += 1;
    if (end - index === width) return true;
    index = end - 1;
  }
  return false;
}

function tableRow(line) {
  const source = line.trim();
  const cells = [];
  let cell = "";
  let ticks = 0;
  let separators = 0;
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (!ticks && character === "\\" && index + 1 < source.length) {
      cell += source.slice(index, index + 2);
      index += 1;
    } else if (character === "`") {
      let end = index + 1;
      while (source[end] === "`") end += 1;
      const width = end - index;
      if (ticks === width) ticks = 0;
      else if (!ticks && hasClosingTicks(source, end, width)) ticks = width;
      cell += source.slice(index, end);
      index = end - 1;
    } else if (character === "|" && !ticks) {
      cells.push(cell.trim());
      cell = "";
      separators += 1;
    } else {
      cell += character;
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
