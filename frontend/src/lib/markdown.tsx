/**
 * A small Markdown renderer that builds React elements, never HTML strings.
 *
 * Model output is untrusted. Everything is rendered as text nodes (React escapes them), raw HTML is
 * not interpreted, and a link is only made for http(s) and mailto URLs, with rel="noopener noreferrer
 * nofollow" and target _blank. Supported: paragraphs, headings, bullet and numbered lists, block
 * quotes, fenced code, inline code, bold, italic, links, and `[S1]`-style citation markers (handed to
 * `renderCitation`, which may return null to leave the marker as plain text).
 */

import { Fragment, type ReactNode } from "react";

export type CitationRenderer = (label: string, key: string) => ReactNode | null;

const INLINE = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\s][^*\n]*\*|\[[^\]\n]+\]\([^)\s]+\)|\[S\d+\])/g;
const MARKER = /^\[(S\d+)\]$/;
const LINK = /^\[([^\]]+)\]\(([^)\s]+)\)$/;

export function safeUrl(raw: string): string | null {
  try {
    const url = new URL(raw);
    return ["http:", "https:", "mailto:"].includes(url.protocol) ? url.toString() : null;
  } catch {
    return null;
  }
}

export function renderInline(text: string, renderCitation: CitationRenderer | undefined, prefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  text.split(INLINE).forEach((part, index) => {
    const key = `${prefix}-${index}`;
    if (!part) return;
    const marker = MARKER.exec(part);
    if (marker) {
      const chip = renderCitation?.(marker[1], key) ?? null;
      nodes.push(chip ?? <Fragment key={key}>{part}</Fragment>);
    } else if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      nodes.push(<code key={key}>{part.slice(1, -1)}</code>);
    } else if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      nodes.push(<strong key={key}>{renderInline(part.slice(2, -2), renderCitation, key)}</strong>);
    } else if (part.startsWith("*") && part.endsWith("*") && part.length > 2 && !part.startsWith("**")) {
      nodes.push(<em key={key}>{renderInline(part.slice(1, -1), renderCitation, key)}</em>);
    } else if (LINK.test(part)) {
      const [, label, target] = LINK.exec(part) as RegExpExecArray;
      const href = safeUrl(target);
      nodes.push(
        href ? (
          <a key={key} href={href} target="_blank" rel="noopener noreferrer nofollow">
            {label}
          </a>
        ) : (
          <Fragment key={key}>{label}</Fragment>
        ),
      );
    } else {
      nodes.push(<Fragment key={key}>{part}</Fragment>);
    }
  });
  return nodes;
}

interface Block {
  kind: "p" | "h" | "ul" | "ol" | "quote" | "code";
  lines: string[];
  level?: number;
}

export function parseBlocks(source: string): Block[] {
  const blocks: Block[] = [];
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const holder: { current: Block | null } = { current: null };
  let fence: string[] | null = null;
  const flush = () => {
    if (holder.current) blocks.push(holder.current);
    holder.current = null;
  };
  for (const line of lines) {
    if (fence) {
      if (line.trim().startsWith("```")) {
        blocks.push({ kind: "code", lines: fence });
        fence = null;
      } else {
        fence.push(line);
      }
      continue;
    }
    if (line.trim().startsWith("```")) {
      flush();
      fence = [];
      continue;
    }
    if (!line.trim()) {
      flush();
      continue;
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    const quote = /^>\s?(.*)$/.exec(line);
    if (heading) {
      flush();
      blocks.push({ kind: "h", lines: [heading[2]], level: heading[1].length });
    } else if (bullet || numbered) {
      const kind = bullet ? "ul" : "ol";
      const text = (bullet ?? numbered)?.[1] ?? "";
      if (holder.current?.kind === kind) holder.current.lines.push(text);
      else {
        flush();
        holder.current = { kind, lines: [text] };
      }
    } else if (quote) {
      if (holder.current?.kind === "quote") holder.current.lines.push(quote[1]);
      else {
        flush();
        holder.current = { kind: "quote", lines: [quote[1]] };
      }
    } else if (holder.current?.kind === "p") {
      holder.current.lines.push(line.trim());
    } else {
      flush();
      holder.current = { kind: "p", lines: [line.trim()] };
    }
  }
  if (fence) blocks.push({ kind: "code", lines: fence });
  flush();
  return blocks;
}

export function Markdown({ text, renderCitation }: { text: string; renderCitation?: CitationRenderer }) {
  return (
    <div className="markdown">
      {parseBlocks(text).map((block, index) => {
        const key = `b${index}`;
        const inline = (line: string, i: number) => renderInline(line, renderCitation, `${key}-${i}`);
        switch (block.kind) {
          case "code":
            return (
              <pre key={key}>
                <code>{block.lines.join("\n")}</code>
              </pre>
            );
          case "h": {
            const Tag = (["h3", "h4", "h5"] as const)[Math.min((block.level ?? 1) - 1, 2)];
            return <Tag key={key}>{inline(block.lines[0], 0)}</Tag>;
          }
          case "ul":
            return (
              <ul key={key}>
                {block.lines.map((line, i) => (
                  <li key={i}>{inline(line, i)}</li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={key}>
                {block.lines.map((line, i) => (
                  <li key={i}>{inline(line, i)}</li>
                ))}
              </ol>
            );
          case "quote":
            return <blockquote key={key}>{block.lines.map((line, i) => inline(line, i))}</blockquote>;
          default:
            return (
              <p key={key}>
                {block.lines.map((line, i) => (
                  <Fragment key={i}>
                    {i > 0 ? " " : null}
                    {inline(line, i)}
                  </Fragment>
                ))}
              </p>
            );
        }
      })}
    </div>
  );
}
