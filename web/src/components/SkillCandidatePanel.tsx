import type { SkillCandidate } from "../types";
export function SkillCandidatePanel({
  candidate,
  onDecision,
}: {
  candidate: SkillCandidate;
  onDecision(id: string, approved: boolean): Promise<void>;
}) {
  return (
    <section className="skill-candidate" aria-label="候选 Skill">
      <header>
        <span className="section-label">SKILL CANDIDATE</span>
        <h3>
          {candidate.name} v{candidate.version}
        </h3>
      </header>
      <p>
        由 {candidate.generated_by_model}{" "}
        从当前运行沉淀。静态校验完成后仍始终禁用，需人工启用。
      </p>
      <pre>{candidate.markdown}</pre>
      {candidate.status === "candidate" ? (
        <div>
          <button
            className="primary-action"
            onClick={() => void onDecision(candidate.id, true)}
          >
            批准候选 Skill
          </button>
          <button onClick={() => void onDecision(candidate.id, false)}>
            拒绝候选 Skill
          </button>
        </div>
      ) : (
        <strong>
          {candidate.status === "approved" ? "已批准，仍保持未启用" : "已拒绝"}
        </strong>
      )}
    </section>
  );
}
