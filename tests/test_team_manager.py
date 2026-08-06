from data_analysis_agent.team import AgentRole, TeamManager, TeamRepository


def test_manager_persists_run_before_submitting(task, db_connection, settings) -> None:
    observed = []
    team_repository = TeamRepository(db_connection)

    class Runner:
        def run(self, run_id):
            observed.append(run_id)
            return None

    manager = TeamManager(team_repository, settings, lambda role, event: Runner())
    run = manager.spawn(task.id, AgentRole.SQL, "Query")
    manager.await_runs((run.id,), 1)
    manager.shutdown()
    assert observed == [run.id]
