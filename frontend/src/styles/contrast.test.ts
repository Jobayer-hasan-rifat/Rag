import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

describe("design tokens", () => {
  it("keep every text and control pair used by the interface at WCAG AA in both themes", () => {
    const script = fileURLToPath(new URL("../../scripts/check-contrast.mjs", import.meta.url));
    const output = execFileSync(process.execPath, [script], { encoding: "utf8" });
    expect(output).toContain("pairs pass");
  });
});
