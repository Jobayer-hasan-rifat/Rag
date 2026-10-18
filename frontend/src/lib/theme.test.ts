import { beforeEach, describe, expect, it, vi } from "vitest";

import { applyTheme, saveTheme, storedTheme } from "./theme";

function storage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (k) => data.get(k) ?? null,
    key: (i) => [...data.keys()][i] ?? null,
    removeItem: (k) => void data.delete(k),
    setItem: (k, v) => void data.set(k, v),
  };
}

function fakeDocument() {
  const attributes = new Map<string, string>();
  return {
    attributes,
    documentElement: {
      setAttribute: (name: string, value: string) => void attributes.set(name, value),
      removeAttribute: (name: string) => void attributes.delete(name),
    },
  };
}

describe("theme preference", () => {
  let doc: ReturnType<typeof fakeDocument>;

  beforeEach(() => {
    vi.stubGlobal("localStorage", storage());
    doc = fakeDocument();
    vi.stubGlobal("document", doc);
  });

  it("defaults to following the system when nothing is stored", () => {
    expect(storedTheme()).toBe("system");
  });

  it("stores light and dark and applies them to the document", () => {
    saveTheme("dark");
    expect(storedTheme()).toBe("dark");
    expect(doc.attributes.get("data-theme")).toBe("dark");
    saveTheme("light");
    expect(doc.attributes.get("data-theme")).toBe("light");
  });

  it("returns to the system setting by removing the override and the stored value", () => {
    saveTheme("dark");
    saveTheme("system");
    expect(storedTheme()).toBe("system");
    expect(doc.attributes.has("data-theme")).toBe(false);
  });

  it("ignores a damaged stored value", () => {
    localStorage.setItem("rag.ui.theme", "purple");
    expect(storedTheme()).toBe("system");
  });

  it("still applies a choice when storage is unavailable", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
      removeItem: () => {
        throw new Error("blocked");
      },
    });
    expect(storedTheme()).toBe("system");
    expect(() => saveTheme("dark")).not.toThrow();
    expect(doc.attributes.get("data-theme")).toBe("dark");
    applyTheme("system");
    expect(doc.attributes.has("data-theme")).toBe(false);
  });
});
