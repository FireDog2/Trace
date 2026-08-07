import type { TaskDetail } from "../types";

interface ArtifactWorkspaceProps {
  task: TaskDetail;
}

export function ArtifactWorkspace({ task }: ArtifactWorkspaceProps) {
  return (
    <aside className="artifact-workspace" aria-label="最终产物">
      <header className="artifact-workspace-header">
        <h2>最终产物</h2>
        <span>{task.artifacts.length} 项</span>
      </header>
    </aside>
  );
}
