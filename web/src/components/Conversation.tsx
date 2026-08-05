import { FormEvent, useState } from "react";

import type { Message, TaskEvent } from "../types";

interface ConversationProps {
  messages: Message[];
  events: TaskEvent[];
  onSend(content: string): Promise<void>;
  onApproval(approvalId: string, approved: boolean): Promise<void>;
  onCollapse(): void;
  hidden?: boolean;
}

interface ApprovalView {
  id: string;
  toolName: string;
  reason: string;
  status: string;
}

export function Conversation({
  messages,
  events,
  onSend,
  onApproval,
  onCollapse,
  hidden = false,
}: ConversationProps) {
  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);
  const [deciding, setDeciding] = useState<string | null>(null);
  const approvals = approvalViews(events);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const message = content.trim();
    if (!message || sending) return;
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

  return (
    <aside className="conversation-panel" hidden={hidden} aria-label="分析协作对话">
      <header>
        <div className="conversation-heading-row">
          <div>
            <div className="section-label">对话入口</div>
            <h2>分析协作</h2>
          </div>
          <button type="button" className="conversation-collapse" onClick={onCollapse}>
            收起对话
          </button>
        </div>
        <p>调整目标、补充口径，或继续追问。</p>
      </header>

      <div className="message-list" aria-live="polite">
        {messages.length === 0 && (
          <p className="empty-note">对话负责表达意图，正式工作会沉淀到左侧任务中。</p>
        )}
        {messages.map((message) => {
          const roleLabel = message.role === "user" ? "你" : message.role === "assistant" ? "Agent" : "执行";
          if (message.role === "tool") {
            return (
              <details key={message.id} className="message tool-message">
                <summary>{roleLabel} · 查看执行详情</summary>
                <p>{message.content}</p>
              </details>
            );
          }
          return (
            <article key={message.id} className={`message ${message.role}`}>
              <span>{roleLabel}</span>
              <p>{message.content}</p>
            </article>
          );
        })}
      </div>

      {approvals.length > 0 && (
        <section className="approval-stack" aria-label="待审批操作">
          {approvals.map((approval) => (
            <article className={`approval-card ${approval.status}`} key={approval.id}>
              <span className="approval-kicker">PERMISSION · {approval.toolName}</span>
              <strong>{approval.status === "pending" ? "需要你的批准" : "审批已处理"}</strong>
              <p>{approval.reason}</p>
              {approval.status === "pending" ? (
                <div className="approval-actions">
                  <button
                    type="button"
                    disabled={deciding === approval.id}
                    aria-label="拒绝操作"
                    onClick={() => void decide(approval.id, false)}
                  >
                    拒绝
                  </button>
                  <button
                    type="button"
                    disabled={deciding === approval.id}
                    aria-label="批准操作"
                    onClick={() => void decide(approval.id, true)}
                  >
                    批准一次
                  </button>
                </div>
              ) : (
                <small>{approval.status === "approved" ? "已批准，任务将继续" : "已拒绝"}</small>
              )}
            </article>
          ))}
        </section>
      )}

      <form className="message-composer" onSubmit={submit}>
        <label htmlFor="agent-message">给分析 Agent 留言</label>
        <textarea
          id="agent-message"
          aria-label="给分析 Agent 留言"
          value={content}
          disabled={sending}
          placeholder="例如：物流成本按发货日期重新计算"
          onChange={(event) => setContent(event.target.value)}
        />
        <div>
          <span>⌘ ↵</span>
          <button type="submit" disabled={sending || !content.trim()}>
            {sending ? "发送中" : "发送指令"}
          </button>
        </div>
      </form>
    </aside>
  );
}

function approvalViews(events: TaskEvent[]): ApprovalView[] {
  const approvals = new Map<string, ApprovalView>();
  for (const event of events) {
    const approvalId = String(event.payload.approval_id ?? "");
    if (!approvalId) continue;
    if (event.type === "approval.requested") {
      approvals.set(approvalId, {
        id: approvalId,
        toolName: String(event.payload.tool_name ?? "unknown action"),
        reason: String(event.payload.reason ?? "该操作超出自动权限边界"),
        status: "pending",
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
