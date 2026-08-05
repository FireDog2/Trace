import { FormEvent, useState } from "react";

import type { Message } from "../types";

interface ConversationProps {
  messages: Message[];
  disabled?: boolean;
  onSend(content: string): Promise<void>;
}

export function Conversation({ messages, disabled, onSend }: ConversationProps) {
  const [content, setContent] = useState("");
  const [sending, setSending] = useState(false);

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

  return (
    <aside className="conversation-panel">
      <header>
        <div className="section-label">对话入口</div>
        <h2>分析协作</h2>
        <p>调整目标、补充口径，或继续追问。</p>
      </header>

      <div className="message-list" aria-live="polite">
        {messages.length === 0 && (
          <p className="empty-note">对话负责表达意图，正式工作会沉淀到左侧任务中。</p>
        )}
        {messages.map((message) => (
          <article key={message.id} className={`message ${message.role}`}>
            <span>{message.role === "user" ? "你" : message.role === "assistant" ? "Agent" : "执行"}</span>
            <p>{message.content}</p>
          </article>
        ))}
      </div>

      <form className="message-composer" onSubmit={submit}>
        <label htmlFor="agent-message">给分析 Agent 留言</label>
        <textarea
          id="agent-message"
          aria-label="给分析 Agent 留言"
          value={content}
          disabled={disabled || sending}
          placeholder="例如：物流成本按发货日期重新计算"
          onChange={(event) => setContent(event.target.value)}
        />
        <div>
          <span>⌘ ↵</span>
          <button type="submit" disabled={disabled || sending || !content.trim()}>
            {sending ? "发送中" : "发送指令"}
          </button>
        </div>
      </form>
    </aside>
  );
}
