import { FormEvent, useState } from "react";

import type { Message, TaskDetail, TaskEvent } from "../types";
import { Markdown } from "./Markdown";

interface ConversationProps {
  task: TaskDetail;
  events: TaskEvent[];
  onUpload(file: File): Promise<void>;
  onSend(content: string): Promise<void>;
  onApproval(approvalId: string, approved: boolean): Promise<void>;
}

const STATUS_LABELS: Record<string, string> = {
  understanding: "Understanding the question",
  planning: "Planning",
  running: "Running analysis",
  synthesizing: "Synthesizing findings",
  needs_input: "Waiting for input",
  failed: "Failed",
  cancelled: "Cancelled",
  completed: "Analysis complete",
};

const ACTIVE_STATUSES = new Set([
  "understanding",
  "planning",
  "running",
  "synthesizing",
]);

interface ApprovalView {
  id: string;
  toolName: string;
  reason: string;
  status: string;
  sequence: number;
}

type ConversationItem =
  | { kind: "message"; message: Message; sequence: number; order: number }
  | {
      kind: "approval";
      approval: ApprovalView;
      sequence: number;
      order: number;
    };

export function Conversation({
  task,
  events,
  onUpload,
  onSend,
  onApproval,
}: ConversationProps) {
  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);
  const [deciding, setDeciding] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadedFileName, setUploadedFileName] = useState("");
  const firstMessage = task.messages[0];
  const persistedObjectiveId =
    firstMessage?.role === "user" &&
    firstMessage.content.trim() === task.objective.trim()
      ? firstMessage.id
      : null;
  const items = conversationItems(
    task.messages.filter((message) => message.id !== persistedObjectiveId),
    events,
  );
  const failed = task.status === "failed";
  const active = ACTIVE_STATUSES.has(task.status);
  const composerLocked = active || task.status === "cancelled";
  const executionRows =
    task.plan?.steps.map((step) => ({
      id: step.id,
      position: step.position,
      title: step.title,
      status: step.status,
    })) ?? [];

  async function submit(event: FormEvent) {
    event.preventDefault();
    const message = content.trim();
    if (!message || sending || composerLocked) return;
    setSending(true);
    try {
      await onSend(message);
      setContent("");
    } finally {
      setSending(false);
    }
  }

  async function decide(approvalId: string, approved: boolean) {
    setDeciding(approvalId);
    try {
      await onApproval(approvalId, approved);
    } finally {
      setDeciding(null);
    }
  }

  async function upload(file: File) {
    setUploading(true);
    try {
      await onUpload(file);
      setUploadedFileName(file.name);
    } finally {
      setUploading(false);
    }
  }

  return (
    <main className="conversation-panel" aria-label="Analysis conversation">
      <header className="conversation-header">
        <div>
          <span>Analysis conversation</span>
          <h1>{task.objective}</h1>
        </div>
        <span className={`task-state ${task.status}`}>
          {STATUS_LABELS[task.status] ?? task.status}
        </span>
      </header>

      <div className="conversation-scroll">
        <div className="message-list conversation-opening">
          <article className="message user">
            <span>You</span>
            <p>{task.objective}</p>
          </article>
        </div>
        {failed && (
          <section className="task-retry-guide" role="alert">
            <span>RETRY AVAILABLE</span>
            <strong>The previous run did not finish</strong>
            <p>
              Check the model connection, then revise or add to the question
              below and run it again. Existing evidence will be preserved.
            </p>
          </section>
        )}
        {active && (
          <section
            className="task-running-guide"
            role="status"
            aria-live="polite"
          >
            <span className="running-indicator" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            <div>
              <strong>The agent is working on this question</strong>
              <p>
                Queries, analysis, and report generation may take a little time.
                Results will appear here automatically when the run finishes.
              </p>
            </div>
          </section>
        )}
        <section className="agent-trace" aria-labelledby="agent-trace-heading">
          <h2 id="agent-trace-heading">Agent execution trace</h2>
          <ol>
            {executionRows.map((row) => (
              <li key={row.id} data-status={row.status}>
                <span>{row.position}</span>
                <strong>{row.title}</strong>
                <small>{STATUS_LABELS[row.status] ?? row.status}</small>
              </li>
            ))}
          </ol>
        </section>

        <div className="message-list" aria-live="polite">
          {items.length === 0 && (
            <p className="empty-note">
              Use the conversation to express your intent. The resulting work
              will be saved to the task on the left.
            </p>
          )}
          {items.map((item) => {
            if (item.kind === "approval") {
              const approval = item.approval;
              return (
                <article
                  className={`approval-card ${approval.status}`}
                  key={`approval-${approval.id}`}
                >
                  <span className="approval-kicker">
                    PERMISSION · {approval.toolName}
                  </span>
                  <strong>
                    {approval.status === "pending"
                      ? "Your approval is required"
                      : "Approval handled"}
                  </strong>
                  <p>{approval.reason}</p>
                  {approval.status === "pending" ? (
                    <div className="approval-actions">
                      <button
                        type="button"
                        disabled={deciding === approval.id}
                        aria-label="Reject action"
                        onClick={() => void decide(approval.id, false)}
                      >
                        Rejected
                      </button>
                      <button
                        type="button"
                        disabled={deciding === approval.id}
                        aria-label="Approve action"
                        onClick={() => void decide(approval.id, true)}
                      >
                        Approve once
                      </button>
                    </div>
                  ) : (
                    <small>
                      {approval.status === "approved"
                        ? "Approved. The task will continue."
                        : "Rejected"}
                    </small>
                  )}
                </article>
              );
            }
            const message = item.message;
            const roleLabel =
              message.role === "user"
                ? "You"
                : message.role === "assistant"
                  ? "Agent"
                  : "Execution";
            if (message.role === "tool") {
              return (
                <details key={message.id} className="message tool-message">
                  <summary>{roleLabel} · View execution details</summary>
                  <p>{message.content}</p>
                </details>
              );
            }
            return (
              <article key={message.id} className={`message ${message.role}`}>
                <span>{roleLabel}</span>
                {message.role === "assistant" ? (
                  <Markdown
                    content={message.content}
                    className="conversation-markdown"
                  />
                ) : (
                  <p>{message.content}</p>
                )}
              </article>
            );
          })}
        </div>
      </div>

      <form className="message-composer" onSubmit={submit}>
        <label htmlFor="agent-message">
          {active
            ? "This question is running"
            : failed
              ? "Revise the question and retry"
              : "Message the analysis agent"}
        </label>
        <textarea
          id="agent-message"
          aria-label={
            active
              ? "This question is running"
              : failed
                ? "Revise the question and retry"
                : "Message the analysis agent"
          }
          value={content}
          disabled={sending || composerLocked}
          placeholder={
            active
              ? "You can follow up when the run finishes"
              : failed
                ? "For example: Retry, and first list every artifact in this project"
                : "For example: Recalculate logistics costs by shipping date"
          }
          onChange={(event) => setContent(event.target.value)}
        />
        <div className="composer-actions">
          <div className="composer-attachments">
            <label
              className={
                uploading ? "composer-upload uploading" : "composer-upload"
              }
              title="Upload analysis data or a business definitions document"
            >
              <span aria-hidden="true">＋</span>
              <input
                type="file"
                accept=".csv,.xlsx,.pdf,.docx,.md,.txt"
                aria-label="Upload analysis file"
                disabled={uploading || composerLocked}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) void upload(file);
                }}
              />
            </label>
            <span className="attachment-status">
              {uploading
                ? "Uploading…"
                : uploadedFileName
                  ? `Added ${uploadedFileName}`
                  : "Add data or definitions"}
            </span>
          </div>
          <span className="send-hint">
            {active ? "Follow up when finished" : "Ctrl ↵"}
          </span>
          <button
            type="submit"
            disabled={sending || composerLocked || !content.trim()}
          >
            {active
              ? "Running…"
              : sending
                ? "Sending"
                : failed
                  ? "Run again"
                  : "Send instruction"}
          </button>
        </div>
      </form>
    </main>
  );
}

function approvalViews(events: TaskEvent[]): ApprovalView[] {
  const approvals = new Map<string, ApprovalView>();
  const orderedEvents = [...events].sort(
    (left, right) => left.sequence - right.sequence,
  );
  for (const event of orderedEvents) {
    const approvalId = String(event.payload.approval_id ?? "");
    if (!approvalId) continue;
    if (event.type === "approval.requested") {
      approvals.set(approvalId, {
        id: approvalId,
        toolName: String(event.payload.tool_name ?? "unknown action"),
        reason: String(
          event.payload.reason ??
            "This action exceeds the automatic permission boundary",
        ),
        status: "pending",
        sequence: event.sequence,
      });
    } else if (event.type === "approval.decided") {
      const current = approvals.get(approvalId);
      if (current) {
        approvals.set(approvalId, {
          ...current,
          status: String(event.payload.status ?? "rejected"),
        });
      }
    }
  }
  return [...approvals.values()];
}

function conversationItems(
  messages: Message[],
  events: TaskEvent[],
): ConversationItem[] {
  const messageSequences = new Map<string, number>();
  for (const event of events) {
    if (event.type !== "message.created") continue;
    const messageId = String(event.payload.message_id ?? "");
    if (messageId) messageSequences.set(messageId, event.sequence);
  }
  const fallbackSequence =
    events.reduce((latest, event) => Math.max(latest, event.sequence), 0) + 1;
  const items: ConversationItem[] = messages.map((message, order) => ({
    kind: "message",
    message,
    sequence: messageSequences.get(message.id) ?? fallbackSequence + order,
    order,
  }));
  items.push(
    ...approvalViews(events).map((approval, order) => ({
      kind: "approval" as const,
      approval,
      sequence: approval.sequence,
      order: messages.length + order,
    })),
  );
  return items.sort(
    (left, right) => left.sequence - right.sequence || left.order - right.order,
  );
}
