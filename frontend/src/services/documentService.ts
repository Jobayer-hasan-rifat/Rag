import type {
  DocumentItem,
  DocumentQuery,
  DocumentVersion,
  Paged,
  VersionUpload,
} from "../types/api";
import { del, download, get, patch, post, toPaged, upload, type Reply } from "./apiClient";

export async function listDocuments(query: DocumentQuery, signal?: AbortSignal): Promise<Paged<DocumentItem>> {
  const reply = await get<DocumentItem[]>("/documents", {
    query: { ...query },
    signal,
  });
  return toPaged(reply);
}

export async function getDocument(id: string, signal?: AbortSignal): Promise<DocumentItem> {
  return (await get<DocumentItem>(`/documents/${id}`, { signal })).data;
}

export const uploadDocument = (
  file: File,
  handlers?: { onProgress?: (fraction: number) => void; signal?: AbortSignal },
): Promise<Reply<DocumentItem>> => upload<DocumentItem>("/documents", file, handlers);

export async function renameDocument(id: string, filename: string): Promise<DocumentItem> {
  return (await patch<DocumentItem>(`/documents/${id}`, { filename })).data;
}

export const deleteDocument = (id: string) => del(`/documents/${id}`);

/** Re-queue the newest version of a document if it failed. */
export async function retryDocument(id: string): Promise<DocumentItem> {
  return (await post<DocumentItem>(`/documents/${id}/retry`)).data;
}

export const downloadDocument = (id: string, filename: string) =>
  download(`/documents/${id}/download`, filename);

// --- versions ---------------------------------------------------------------------------------

export async function listVersions(id: string, signal?: AbortSignal): Promise<DocumentVersion[]> {
  return (await get<DocumentVersion[]>(`/documents/${id}/versions`, { query: { page_size: 100 }, signal })).data;
}

export const uploadVersion = (
  id: string,
  file: File,
  handlers?: { onProgress?: (fraction: number) => void; signal?: AbortSignal },
): Promise<Reply<VersionUpload>> => upload<VersionUpload>(`/documents/${id}/versions`, file, handlers);

export async function activateVersion(id: string, versionId: string): Promise<DocumentVersion> {
  return (await post<DocumentVersion>(`/documents/${id}/versions/${versionId}/activate`)).data;
}

export async function retryVersion(id: string, versionId: string): Promise<DocumentVersion> {
  return (await post<DocumentVersion>(`/documents/${id}/versions/${versionId}/retry`)).data;
}

export const deleteVersion = (id: string, versionId: string) => del(`/documents/${id}/versions/${versionId}`);
