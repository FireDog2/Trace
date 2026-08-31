import type { AgentRun } from "../types";
const roles = {
  knowledge: "Knowledge Agent",
  sql: "SQL Agent",
  report: "Report Agent",
  curator: "Curator Agent",
};
export function AgentTeamPanel({ agents }: { agents: AgentRun[] }) {
  return (
    <section aria-label="Agent team">
      <div className="panel-heading">
        <h2>Agent team</h2>
        <span>{agents.length} ACTIVE RECORDS</span>
      </div>
      {agents.length ? (
        <ul className="agent-team-list">
          {agents.map((agent) => (
            <li key={agent.id}>
              <div>
                <span className="agent-role">{roles[agent.role]}</span>
                <span className={`agent-status ${agent.status}`}>
                  {agent.status}
                </span>
              </div>
              <strong>{agent.objective}</strong>
              {agent.result_summary && <p>{agent.result_summary}</p>}
              {agent.error_category && <code>{agent.error_category}</code>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="empty-note">
          The lead agent has not delegated to any teammates yet. They will
          appear here when SQL is executed, definitions are retrieved, or
          reports are generated.
        </p>
      )}
    </section>
  );
}
