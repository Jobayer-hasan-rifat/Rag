// Checks the text/background pairs the interface actually uses, in both themes, against WCAG AA.
// It reads the token values from src/styles/tokens.css, so a changed token is re-checked.
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../src/styles/tokens.css", import.meta.url), "utf8");

function block(startMarker) {
  const start = css.indexOf(startMarker);
  if (start < 0) throw new Error(`missing ${startMarker}`);
  const open = css.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    if (css[i] === "}") {
      depth -= 1;
      if (depth === 0) return css.slice(open + 1, i);
    }
  }
  throw new Error("unbalanced");
}

function tokens(text) {
  const out = {};
  for (const match of text.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) out[match[1]] = match[2];
  return out;
}

const light = tokens(block(":root {"));
const dark = { ...light, ...tokens(block(':root[data-theme="dark"]')) };

function luminance(hex) {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
const ratio = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

// [foreground, background, minimum ratio, where it is used]
const PAIRS = [
  ["text", "bg", 4.5, "body text on the page"],
  ["text", "surface", 4.5, "body text on cards"],
  ["text", "surface-2", 4.5, "text on raised rows"],
  ["text", "sunken", 4.5, "text on sunken areas"],
  ["text-muted", "bg", 4.5, "secondary text on the page"],
  ["text-muted", "surface", 4.5, "secondary text on cards"],
  ["text-muted", "surface-2", 4.5, "secondary text on rows"],
  ["text-subtle", "bg", 4.5, "meta text on the page"],
  ["text-subtle", "surface", 4.5, "meta text on cards"],
  ["text-subtle", "sunken", 4.5, "meta text on sunken areas"],
  ["primary", "bg", 4.5, "links on the page"],
  ["primary", "surface", 4.5, "links on cards"],
  ["primary", "primary-soft", 4.5, "active navigation, citation chips"],
  ["text-on-primary", "primary-solid", 4.5, "primary button label"],
  ["text-on-primary", "primary-solid-hover", 4.5, "primary button label (hover)"],
  ["ok", "ok-soft", 4.5, "ready status"],
  ["warn", "warn-soft", 4.5, "processing and earlier-version notes"],
  ["bad", "bad-soft", 4.5, "failure status and errors"],
  ["bad", "surface", 4.5, "error text on cards"],
  ["ok", "surface", 4.5, "ok text on cards"],
  ["warn", "surface", 4.5, "warning text on cards"],
  ["focus", "bg", 3, "focus ring on the page"],
  ["focus", "surface", 3, "focus ring on cards"],
  ["control-border", "surface", 3, "input and checkbox borders (non-text contrast)"],
  ["control-border", "bg", 3, "input borders on the page"],
];

let failed = 0;
for (const [name, theme] of [["light", light], ["dark", dark]]) {
  for (const [fg, bg, min, use] of PAIRS) {
    const value = ratio(theme[fg], theme[bg]);
    const ok = value >= min;
    if (!ok) failed += 1;
    if (!ok || process.argv.includes("--verbose")) {
      console.log(`${ok ? "ok  " : "FAIL"} ${name.padEnd(5)} ${fg} on ${bg}: ${value.toFixed(2)} (needs ${min}) - ${use}`);
    }
  }
}
console.log(failed === 0 ? `All ${PAIRS.length * 2} pairs pass.` : `${failed} pair(s) fail.`);
process.exit(failed === 0 ? 0 : 1);
