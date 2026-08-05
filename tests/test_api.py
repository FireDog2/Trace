from __future__ import annotations

from fastapi.testclient import TestClient

from data_analysis_agent.api import create_app
from data_analysis_agent.domain import TaskStatus
from data_analysis_agent.permissions import ToolAction
from data_analysis_agent.providers import ModelResponse, ScriptedProvider


def test_project_dataset_task_lifecycle(api_client, operations_csv) -> None:
    project_response = api_client.post(
        "/api/projects",
        json={"name": "Operations"},
    )
    assert project_response.status_code == 201
    project = project_response.json()

    with operations_csv.open("rb") as source:
        dataset_response = api_client.post(
            f"/api/projects/{project['id']}/datasets",
            files={"file": ("operations.csv", source, "text/csv")},
        )
    assert dataset_response.status_code == 201
    dataset = dataset_response.json()

    task_response = api_client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"objective": "Why did East profit decline?"},
    )
    assert task_response.status_code == 201
    task = task_response.json()

    assert dataset["artifact_type"] == "source_dataset"
    assert api_client.get(
        f"/api/projects/{project['id']}/artifacts"
    ).json()[0]["title"] == "operations.csv"
    assert task["status"] == "understanding"
    assert api_client.get(f"/api/tasks/{task['id']}").status_code == 200
    assert api_client.get(
        f"/api/artifacts/{dataset['id']}/dataset-inspection"
    ).json()["row_count"] == 12


def test_events_endpoint_replays_after_sequence(api_client) -> None:
    project = api_client.post(
        "/api/projects",
        json={"name": "Operations"},
    ).json()
    task = api_client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"objective": "Why did East profit decline?"},
    ).json()
    repository = api_client.app.state.services.tasks
    repository.append_event(task["id"], "task.started", {})
    repository.append_event(task["id"], "plan.updated", {"revision": 1})

    response = api_client.get(f"/api/tasks/{task['id']}/events?after=1")

    assert response.status_code == 200
    assert "id: 2" in response.text
    assert "event: plan.updated" in response.text
    assert "id: 1" not in response.text


def test_completed_task_accepts_follow_up_after_application_restart(settings) -> None:
    provider = ScriptedProvider(
        [
            ModelResponse(text="Initial diagnosis complete.", tool_calls=()),
            ModelResponse(text="Follow-up diagnosis complete.", tool_calls=()),
        ]
    )
    with TestClient(create_app(settings, provider)) as first_client:
        project = first_client.post(
            "/api/projects",
            json={"name": "Operating review"},
        ).json()
        task = first_client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"objective": "Why did East profit decline?"},
        ).json()
        assert first_client.get(f"/api/tasks/{task['id']}").json()["status"] == "completed"

    with TestClient(create_app(settings, provider)) as restarted_client:
        response = restarted_client.post(
            f"/api/tasks/{task['id']}/messages",
            json={"content": "Now compare the East result with West."},
        )

        assert response.status_code == 200
        detail = restarted_client.get(f"/api/tasks/{task['id']}").json()
        assert detail["status"] == "completed"
        assert [message["content"] for message in detail["messages"]][-2:] == [
            "Now compare the East result with West.",
            "Follow-up diagnosis complete.",
        ]


def test_approval_decision_is_visible_and_resumes_configured_agent(settings) -> None:
    provider = ScriptedProvider(
        [ModelResponse(text="Approved action handled.", tool_calls=())]
    )
    app = create_app(settings, provider)
    services = app.state.services
    project = services.projects.create("Approval review")
    task = services.tasks.create(project.id, "Use an external benchmark")
    services.tasks.transition(task.id, TaskStatus.NEEDS_INPUT)
    approval = services.approvals.request(
        task.id,
        ToolAction("network_access", {"url": "https://example.invalid"}),
        "External network access requires approval",
    )

    with TestClient(app) as client:
        response = client.post(
            f"/api/approvals/{approval.id}/decision",
            json={"approved": True},
        )

        assert response.status_code == 200
        assert client.get(f"/api/tasks/{task.id}").json()["status"] == "completed"
        events = client.get(f"/api/tasks/{task.id}/events").text
        assert "event: approval.decided" in events
        assert '"status": "approved"' in events
