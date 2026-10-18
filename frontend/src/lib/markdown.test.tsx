import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Markdown, safeUrl } from "./markdown";

const html = (text: string, cite = false) =>
  renderToStaticMarkup(
    <Markdown text={text} renderCitation={cite ? (label, key) => <button key={key}>cite-{label}</button> : undefined} />,
  );

describe("Markdown renderer", () => {
  it("never interprets HTML from the model", () => {
    const out = html('<script>alert(1)</script> <img src=x onerror=alert(1)> **bold**');
    expect(out).not.toContain("<script");
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;script&gt;");
    expect(out).toContain("<strong>bold</strong>");
  });

  it("links only http, https and mailto", () => {
    expect(safeUrl("https://example.org/a")).toBe("https://example.org/a");
    expect(safeUrl("mailto:a@b.co")).toBe("mailto:a@b.co");
    expect(safeUrl("javascript:alert(1)")).toBeNull();
    expect(safeUrl("data:text/html,<b>x</b>")).toBeNull();
    const out = html("[bad](javascript:alert(1)) and [good](https://example.org)");
    expect(out).not.toContain("javascript:");
    expect(out).toContain('rel="noopener noreferrer nofollow"');
    expect(out).toContain('target="_blank"');
  });

  it("renders lists, headings, code blocks and inline code as text", () => {
    const out = html("# Title\n\n- one\n- two\n\n1. first\n2. second\n\n```\n<b>not bold</b>\n```\n\nuse `x < y`");
    expect(out).toContain("<h3>Title</h3>");
    expect(out).toContain("<ul><li>one</li><li>two</li></ul>");
    expect(out).toContain("<ol><li>first</li><li>second</li></ol>");
    expect(out).toContain("<pre><code>&lt;b&gt;not bold&lt;/b&gt;</code></pre>");
    expect(out).toContain("<code>x &lt; y</code>");
  });

  it("hands source markers to the citation renderer and leaves unknown ones as text", () => {
    const out = html("Seedlings go out in July [S1]. See [S9].", true);
    expect(out).toContain("cite-S1");
    expect(out).toContain("cite-S9"); // the renderer decides; the real one returns null for unknown labels
    const plain = html("Seedlings go out in July [S1].");
    expect(plain).toContain("[S1]");
  });

  it("keeps Bangla text intact", () => {
    expect(html("ইলিশ মাছ বর্ষাকালে ধরা পড়ে। [S1]")).toContain("ইলিশ মাছ বর্ষাকালে ধরা পড়ে।");
  });
});
