import { createElement, Fragment, type ReactNode } from "react";

interface MarkdownProps {
  content: string;
  className?: string;
}

const HEADING = /^(#{1,6})\s+(.+)$/;
const LIST = /^\s*([-+*]|\d+[.)])\s+(.+)$/;
const TABLE_RULE = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/;
const INLINE =
  /(`[^`]+`|\*\*[^*]+\*\*|(?<!\*)\*[^*]+\*(?!\*)|\[[^\]]+\]\([^)]+\))/g;

export function Markdown({ content, className = "" }: MarkdownProps) {
  const lines = content
    .replaceAll("\r\n", "\n")
    .replaceAll("\r", "\n")
    .split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;
  let block = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();
    if (!trimmed) {
      index += 1;
      continue;
    }
    if (trimmed === "---" || trimmed === "***" || trimmed === "___") {
      blocks.push(<hr key={`block-${block++}`} />);
      index += 1;
      continue;
    }
    if (trimmed.startsWith("```")) {
      const language = trimmed.slice(3).trim();
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        code.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(
        <pre key={`block-${block++}`} data-language={language || undefined}>
          <code>{code.join("\n")}</code>
        </pre>,
      );
      continue;
    }
    const heading = HEADING.exec(trimmed);
    if (heading) {
      const level = Math.min(heading[1].length + 2, 6);
      const headingTag = `h${level}` as "h3" | "h4" | "h5" | "h6";
      blocks.push(
        createElement(
          headingTag,
          { key: `block-${block++}` },
          inline(heading[2], `heading-${block}`),
        ),
      );
      index += 1;
      continue;
    }
    if (
      index + 1 < lines.length &&
      trimmed.includes("|") &&
      TABLE_RULE.test(lines[index + 1])
    ) {
      const rows = [tableCells(line)];
      index += 2;
      while (
        index < lines.length &&
        lines[index].trim() &&
        lines[index].includes("|")
      ) {
        rows.push(tableCells(lines[index]));
        index += 1;
      }
      blocks.push(<MarkdownTable rows={rows} key={`block-${block++}`} />);
      continue;
    }
    const listMatch = LIST.exec(line);
    if (listMatch) {
      const ordered = /^\d/.test(listMatch[1]);
      const items: string[] = [];
      while (index < lines.length) {
        const item = LIST.exec(lines[index]);
        if (!item || /^\d/.test(item[1]) !== ordered) break;
        items.push(item[2]);
        index += 1;
      }
      const List = ordered ? "ol" : "ul";
      blocks.push(
        <List key={`block-${block++}`}>
          {items.map((item, itemIndex) => (
            <li key={itemIndex}>
              {inline(
                item.replace(/^\[[ xX]\]\s*/, (check) =>
                  check.toLowerCase().includes("x") ? "✓ " : "○ ",
                ),
                `list-${block}-${itemIndex}`,
              )}
            </li>
          ))}
        </List>,
      );
      continue;
    }
    if (trimmed.startsWith(">")) {
      const quote: string[] = [];
      while (index < lines.length && lines[index].trim().startsWith(">")) {
        quote.push(lines[index].trim().replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push(
        <blockquote key={`block-${block++}`}>
          {inline(quote.join(" "), `quote-${block}`)}
        </blockquote>,
      );
      continue;
    }

    const paragraph = [trimmed];
    index += 1;
    while (
      index < lines.length &&
      lines[index].trim() &&
      !startsBlock(lines, index)
    ) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(
      <p key={`block-${block++}`}>
        {inline(paragraph.join(" "), `paragraph-${block}`)}
      </p>,
    );
  }

  return <div className={`markdown-content ${className}`.trim()}>{blocks}</div>;
}

function MarkdownTable({ rows }: { rows: string[][] }) {
  const [head = [], ...body] = rows;
  return (
    <div className="markdown-table-scroll">
      <table>
        <thead>
          <tr>
            {head.map((cell, index) => (
              <th key={index}>{inline(cell, `th-${index}`)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => (
                <td key={cellIndex}>
                  {inline(cell, `td-${rowIndex}-${cellIndex}`)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function startsBlock(lines: string[], index: number) {
  const trimmed = lines[index].trim();
  return (
    trimmed.startsWith("```") ||
    trimmed.startsWith(">") ||
    HEADING.test(trimmed) ||
    LIST.test(lines[index]) ||
    ["---", "***", "___"].includes(trimmed) ||
    (index + 1 < lines.length &&
      trimmed.includes("|") &&
      TABLE_RULE.test(lines[index + 1]))
  );
}

function tableCells(line: string) {
  return line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => cell.trim());
}

function inline(source: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let part = 0;
  INLINE.lastIndex = 0;
  for (const match of source.matchAll(INLINE)) {
    const start = match.index ?? 0;
    if (start > cursor)
      nodes.push(
        <Fragment key={`${keyPrefix}-${part++}`}>
          {source.slice(cursor, start)}
        </Fragment>,
      );
    const token = match[0];
    if (token.startsWith("**")) {
      nodes.push(
        <strong key={`${keyPrefix}-${part++}`}>{token.slice(2, -2)}</strong>,
      );
    } else if (token.startsWith("*")) {
      nodes.push(<em key={`${keyPrefix}-${part++}`}>{token.slice(1, -1)}</em>);
    } else if (token.startsWith("`")) {
      nodes.push(
        <code key={`${keyPrefix}-${part++}`}>{token.slice(1, -1)}</code>,
      );
    } else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      if (link) {
        const href = safeHref(link[2]);
        nodes.push(
          href ? (
            <a
              key={`${keyPrefix}-${part++}`}
              href={href}
              target={href.startsWith("http") ? "_blank" : undefined}
              rel={href.startsWith("http") ? "noreferrer" : undefined}
            >
              {link[1]}
            </a>
          ) : (
            <Fragment key={`${keyPrefix}-${part++}`}>{link[1]}</Fragment>
          ),
        );
      }
    }
    cursor = start + token.length;
  }
  if (cursor < source.length)
    nodes.push(
      <Fragment key={`${keyPrefix}-${part}`}>{source.slice(cursor)}</Fragment>,
    );
  return nodes;
}

function safeHref(target: string) {
  if (target.startsWith("artifact://")) {
    return `/api/artifacts/${encodeURIComponent(target.slice("artifact://".length))}/content`;
  }
  return target.startsWith("https://") || target.startsWith("http://")
    ? target
    : null;
}
