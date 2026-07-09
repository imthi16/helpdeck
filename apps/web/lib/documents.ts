import { api, API_URL } from "@/lib/api";

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";
export type SourceType = "pdf" | "url" | "text";

export interface DocumentItem {
  id: string;
  title: string;
  source_type: SourceType;
  status: DocumentStatus;
  error: string | null;
  chunk_count: number;
  created_at: string;
}

export function listDocuments(): Promise<DocumentItem[]> {
  return api<DocumentItem[]>("/api/v1/documents");
}

export async function uploadPdf(file: File): Promise<DocumentItem> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_URL}/api/v1/documents/upload`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // keep statusText
    }
    throw new Error(detail);
  }
  return (await response.json()) as DocumentItem;
}

export function addUrl(url: string, title?: string): Promise<DocumentItem> {
  return api<DocumentItem>("/api/v1/documents", {
    method: "POST",
    body: JSON.stringify({ source_type: "url", url, title }),
  });
}

export function addText(title: string, content: string): Promise<DocumentItem> {
  return api<DocumentItem>("/api/v1/documents", {
    method: "POST",
    body: JSON.stringify({ source_type: "text", title, content }),
  });
}

export function deleteDocument(id: string): Promise<void> {
  return api<void>(`/api/v1/documents/${id}`, { method: "DELETE" });
}

export function reindexDocument(id: string): Promise<DocumentItem> {
  return api<DocumentItem>(`/api/v1/documents/${id}/reindex`, { method: "POST" });
}
