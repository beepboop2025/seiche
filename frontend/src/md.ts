// Compact markdown -> HTML for dispatch bodies. Content is authored by the
// desk, but we still escape defensively and refuse dangerous URL schemes so
// a stray link or a future content pipeline can't inject script. No
// dependency — same stdlib-only ethos as the backend.
function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function inline(s: string): string {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_m, text, url) => {
      // url is already entity-escaped by esc(); allow only safe schemes.
      const u = String(url).trim();
      const safe = /^(https?:|mailto:|#|\/)/i.test(u) ? u : "#";
      return `<a href="${safe}" target="_blank" rel="noopener noreferrer">${text}</a>`;
    });
}

export function renderMarkdown(src: string): string {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const out: string[] = [];
  let i = 0;
  let inList = false;
  const closeList = () => { if (inList) { out.push("</ul>"); inList = false; } };

  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      closeList();
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++;
      out.push("<pre><code>" + esc(buf.join("\n")) + "</code></pre>");
      continue;
    }
    if (/^\s*$/.test(line)) { closeList(); i++; continue; }
    if (/^###\s+/.test(line)) { closeList(); out.push("<h3>" + inline(line.replace(/^###\s+/, "")) + "</h3>"); i++; continue; }
    if (/^##\s+/.test(line)) { closeList(); out.push("<h2>" + inline(line.replace(/^##\s+/, "")) + "</h2>"); i++; continue; }
    if (/^---\s*$/.test(line)) { closeList(); out.push("<hr />"); i++; continue; }
    if (/^>\s?/.test(line)) { closeList(); out.push("<blockquote>" + inline(line.replace(/^>\s?/, "")) + "</blockquote>"); i++; continue; }
    if (/^[-*]\s+/.test(line)) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push("<li>" + inline(line.replace(/^[-*]\s+/, "")) + "</li>");
      i++; continue;
    }
    closeList();
    out.push("<p>" + inline(line) + "</p>");
    i++;
  }
  closeList();
  return out.join("\n");
}
