from __future__ import annotations


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
