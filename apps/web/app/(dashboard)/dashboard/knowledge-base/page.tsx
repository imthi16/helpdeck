"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { PageHeader } from "@/components/page-header";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  addText,
  addUrl,
  deleteDocument,
  listDocuments,
  reindexDocument,
  uploadPdf,
  type DocumentItem,
  type DocumentStatus,
} from "@/lib/documents";

const STATUS_VARIANT: Record<DocumentStatus, "default" | "secondary" | "destructive"> = {
  pending: "secondary",
  processing: "secondary",
  ready: "default",
  failed: "destructive",
};

function StatusBadge({ status }: { status: DocumentStatus }) {
  return (
    <Badge variant={STATUS_VARIANT[status]} data-testid="doc-status">
      {status}
    </Badge>
  );
}

function UploadZone({ onUpload }: { onUpload: (file: File) => Promise<void> }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  async function handleFiles(files: FileList | null) {
    const file = files?.[0];
    if (file) await onUpload(file);
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => inputRef.current?.click()}
      onKeyDown={(e) => e.key === "Enter" && inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        void handleFiles(e.dataTransfer.files);
      }}
      className={`flex cursor-pointer flex-col items-center justify-center rounded-md border border-dashed p-8 text-sm text-muted-foreground transition-colors ${
        dragging ? "border-primary bg-accent" : ""
      }`}
      data-testid="upload-zone"
    >
      <p>Drag a PDF here, or click to choose a file.</p>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        className="hidden"
        data-testid="file-input"
        onChange={(e) => void handleFiles(e.target.files)}
      />
    </div>
  );
}

export default function KnowledgeBasePage() {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [textTitle, setTextTitle] = useState("");
  const [textBody, setTextBody] = useState("");
  const [pendingDelete, setPendingDelete] = useState<DocumentItem | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDocuments(await listDocuments());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load documents");
    }
  }, []);

  // Initial load and polling while any document is still ingesting.
  useEffect(() => {
    let active = true;
    const load = () => {
      listDocuments()
        .then((docs) => {
          if (active) setDocuments(docs);
        })
        .catch((err) => {
          if (active) setError(err instanceof Error ? err.message : "Failed to load documents");
        });
    };
    load();
    const timer = setInterval(load, 1500);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    }
  }

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        title="Knowledge Base"
        description="Upload documents and manage the sources your assistant answers from."
      />

      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="text-base">Add a source</CardTitle>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="pdf">
            <TabsList>
              <TabsTrigger value="pdf">Upload PDF</TabsTrigger>
              <TabsTrigger value="url">Add URL</TabsTrigger>
              <TabsTrigger value="text">Paste text</TabsTrigger>
            </TabsList>
            <TabsContent value="pdf" className="pt-4">
              <UploadZone onUpload={(file) => run(() => uploadPdf(file))} />
            </TabsContent>
            <TabsContent value="url" className="pt-4">
              <form
                className="flex items-end gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(async () => {
                    await addUrl(url);
                    setUrl("");
                  });
                }}
              >
                <div className="flex-1">
                  <Label htmlFor="url">Page URL</Label>
                  <Input
                    id="url"
                    type="url"
                    required
                    placeholder="https://docs.example.com/faq"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                  />
                </div>
                <Button type="submit">Add</Button>
              </form>
            </TabsContent>
            <TabsContent value="text" className="pt-4">
              <form
                className="flex flex-col gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(async () => {
                    await addText(textTitle, textBody);
                    setTextTitle("");
                    setTextBody("");
                  });
                }}
              >
                <Label htmlFor="text-title">Title</Label>
                <Input
                  id="text-title"
                  required
                  value={textTitle}
                  onChange={(e) => setTextTitle(e.target.value)}
                />
                <Label htmlFor="text-body">Content</Label>
                <Textarea
                  id="text-body"
                  required
                  rows={5}
                  value={textBody}
                  onChange={(e) => setTextBody(e.target.value)}
                />
                <Button type="submit" className="self-start">
                  Add text
                </Button>
              </form>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {error && (
        <p role="alert" className="mb-4 text-sm text-red-600">
          {error}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Documents</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Chunks</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {documents.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center text-muted-foreground">
                    No documents yet.
                  </TableCell>
                </TableRow>
              ) : (
                documents.map((doc) => (
                  <TableRow key={doc.id} data-testid="doc-row" data-title={doc.title}>
                    <TableCell className="font-medium">{doc.title}</TableCell>
                    <TableCell className="uppercase">{doc.source_type}</TableCell>
                    <TableCell>
                      <StatusBadge status={doc.status} />
                    </TableCell>
                    <TableCell className="text-right" data-testid="doc-chunks">
                      {doc.chunk_count}
                    </TableCell>
                    <TableCell className="flex justify-end gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => run(() => reindexDocument(doc.id))}
                      >
                        Re-index
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        data-testid="doc-delete"
                        onClick={() => setPendingDelete(doc)}
                      >
                        Delete
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this document?</AlertDialogTitle>
            <AlertDialogDescription>
              “{pendingDelete?.title}” and its chunks will be removed permanently.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              data-testid="confirm-delete"
              onClick={() => {
                const doc = pendingDelete;
                setPendingDelete(null);
                if (doc) void run(() => deleteDocument(doc.id));
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
