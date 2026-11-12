// A small Markdown for notes: headings, lists (also nested and with [ ] boxes), quotes, rules,
// tables, **bold**, *italic*, `code`, ~~struck~~ and links. Builds DOM nodes, never HTML text, so
// nothing typed into a sheet can run as a script. Links open only for http(s), mailto and tel.

const node = (tag, attrs = {}, children = []) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  el.append(...children);
  return el;
};

const INLINE = /(\*\*|__)(.+?)\1|(\*|_)(?=\S)(.+?)(?<=\S)\3|`([^`]+)`|~~(.+?)~~|\[([^\]]+)\]\(([^)\s]+)\)/;

export function inline(text) {
  const out = [];
  let rest = text;
  while (rest) {
    const m = INLINE.exec(rest);
    if (!m) { out.push(rest); break; }
    if (m.index) out.push(rest.slice(0, m.index));
    if (m[2] != null) out.push(node("strong", {}, inline(m[2])));
    else if (m[4] != null) out.push(node("em", {}, inline(m[4])));
    else if (m[5] != null) out.push(node("code", {}, [m[5]]));
    else if (m[6] != null) out.push(node("s", {}, inline(m[6])));
    else {
      const safe = /^(https?:|mailto:|tel:)/i.test(m[8]);
      out.push(safe ? node("a", { href: m[8], target: "_blank", rel: "noopener noreferrer" }, inline(m[7])) : `${m[7]} (${m[8]})`);
    }
    rest = rest.slice(m.index + m[0].length);
  }
  return out;
}

// One block element per paragraph, heading, quote or list.
// A Markdown table: | a | b | rows, the second line made of dashes (| --- | :-: |).
const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
const isDivider = (line) => /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(line) && line.includes("-") && line.includes("|");

function table(rows) {
  const [head, , ...body] = rows.map(cells);
  const width = head.length;
  const row = (values, tag) => node("tr", {}, Array.from({ length: width }, (_, i) => node(tag, {}, inline(values[i] || ""))));
  return node("div", { class: "table-wrap" }, [node("table", { class: "md-table" }, [
    node("thead", {}, [row(head, "th")]), node("tbody", {}, body.map((values) => row(values, "td")))])]);
}

export function markdown(text) {
  const blocks = [];
  const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  let paragraph = [];
  let stack = [];  // open lists: { depth, el }
  const flush = () => {
    if (paragraph.length) {
      const p = node("p");
      paragraph.forEach((line, i) => { if (i) p.append(node("br")); p.append(...inline(line)); });
      blocks.push(p);
    }
    paragraph = [];
  };
  const closeLists = () => { stack = []; };
  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index];
    if (raw.includes("|") && index + 1 < lines.length && isDivider(lines[index + 1])) {
      flush();
      closeLists();
      const rows = [raw, lines[index + 1]];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) rows.push(lines[index++]);
      index -= 1;
      blocks.push(table(rows));
      continue;
    }
    const item = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(raw);
    if (item) {
      flush();
      const depth = Math.floor(item[1].replace(/\t/g, "  ").length / 2);
      const ordered = /\d/.test(item[2]);
      while (stack.length && stack[stack.length - 1].depth > depth) stack.pop();
      let top = stack[stack.length - 1];
      if (!top || top.depth < depth) {
        const list = node(ordered ? "ol" : "ul");
        if (top) (top.el.lastElementChild || top.el).append(list); else blocks.push(list);
        top = { depth, el: list };
        stack.push(top);
      }
      const box = /^\[([ xX])\]\s+(.*)$/.exec(item[3]);
      const li = node("li", box ? { class: "task" } : {});
      if (box) li.append(box[1] === " " ? "☐ " : "☑ ", ...inline(box[2])); else li.append(...inline(item[3]));
      top.el.append(li);
      continue;
    }
    if (!raw.trim()) { flush(); closeLists(); continue; }
    closeLists();
    const heading = /^\s*(#{1,6})\s+(.*)$/.exec(raw);
    if (heading) { flush(); blocks.push(node(`h${Math.min(6, heading[1].length + 2)}`, {}, inline(heading[2]))); continue; }
    if (/^\s*([-*_]\s*){3,}$/.test(raw)) { flush(); blocks.push(node("hr")); continue; }
    const quote = /^\s*>\s?(.*)$/.exec(raw);
    if (quote) { flush(); blocks.push(node("blockquote", {}, inline(quote[1]))); continue; }
    paragraph.push(raw.trim());
  }
  flush();
  return node("div", { class: "md" }, blocks);
}
