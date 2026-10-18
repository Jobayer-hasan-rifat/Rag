import { beforeEach, describe, expect, it, vi } from "vitest";

import { sourceFromCitation } from "../components/SourcePanel";
import type { MessageCitation } from "../types/api";
import { formatBytes, hasSupportedExtension, isProcessing } from "./format";
import { clearPending, loadPending, savePending } from "./pending";

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

const citation = (change: Partial<MessageCitation>): MessageCitation => ({
  label: "S1",
  document_id: "d",
  version_id: "v1",
  version_number: 1,
  chunk_id: "c",
  document_name: "rice.txt",
  chunk_index: 0,
  section_ordinal: 0,
  page_number: null,
  heading: null,
  heading_path: [],
  start_char: 0,
  end_char: 10,
  excerpt: "Seedlings are transplanted in July.",
  excerpt_truncated: false,
  source_truncated: false,
  source_available: true,
  removed: false,
  superseded: false,
  ...change,
});

describe("source inspection", () => {
  it("shows an earlier version's own text and says it is earlier", () => {
    const view = sourceFromCitation(citation({ superseded: true, version_number: 1 }));
    expect(view.text).toBe("Seedlings are transplanted in July.");
    expect(view.versionNumber).toBe(1);
    expect(view.notes.some((n) => n.text.includes("earlier version"))).toBe(true);
  });

  it("shows no text for a deleted source and does not link to it", () => {
    const view = sourceFromCitation(citation({ removed: true, source_available: false, excerpt: null, document_name: null }));
    expect(view.text).toBeNull();
    expect(view.documentId).toBeNull();
    expect(view.title).toBe("Deleted source");
    expect(view.notes[0].tone).toBe("warn");
  });

  it("distinguishes an unavailable passage from a deleted one", () => {
    const view = sourceFromCitation(citation({ source_available: false, excerpt: null }));
    expect(view.text).toBeNull();
    expect(view.documentId).toBe("d");
    expect(view.notes[0].text).toMatch(/can no longer be shown/);
  });

  it("labels each kind of source so the panel never calls an old version active", () => {
    expect(sourceFromCitation(citation({})).state).toBe("active");
    expect(sourceFromCitation(citation({ superseded: true })).state).toBe("earlier");
    expect(sourceFromCitation(citation({ source_available: false, excerpt: null })).state).toBe("unavailable");
    expect(sourceFromCitation(citation({ removed: true, source_available: false, excerpt: null })).state).toBe("deleted");
  });

  it("carries the answer's marker so the panel can say which source is open", () => {
    expect(sourceFromCitation(citation({ label: "S3" })).label).toBe("S3");
  });

  it("explains answers saved before versions existed", () => {
    const view = sourceFromCitation(citation({ version_id: null, version_number: null }));
    expect(view.notes.some((n) => n.text.includes("first version"))).toBe(true);
  });
});

describe("pending question store", () => {
  beforeEach(() => vi.stubGlobal("sessionStorage", storage()));

  it("keeps the idempotency key for a retry and forgets it when cleared", () => {
    savePending("c1", { key: "q-1", content: "When?", documentIds: ["d"], rerank: false, startedAt: 1 });
    expect(loadPending("c1")).toMatchObject({ key: "q-1", content: "When?" });
    expect(loadPending("c2")).toBeNull();
    clearPending("c1");
    expect(loadPending("c1")).toBeNull();
  });

  it("ignores damaged records", () => {
    sessionStorage.setItem("rag.pending.c1", "{not json");
    expect(loadPending("c1")).toBeNull();
  });
});

describe("formatting and file rules", () => {
  it("formats sizes and recognises processing states", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(isProcessing("embedding")).toBe(true);
    expect(isProcessing("ready")).toBe(false);
    expect(isProcessing("failed")).toBe(false);
  });

  it("accepts only the supported extensions", () => {
    for (const name of ["a.pdf", "A.DOCX", "n.txt", "r.md"]) expect(hasSupportedExtension(name)).toBe(true);
    for (const name of ["a.exe", "b.docx.exe", "c"]) expect(hasSupportedExtension(name)).toBe(false);
  });
});
