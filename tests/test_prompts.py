from __future__ import annotations

from data_analysis_agent.prompts import PromptContext, assemble_prompt


def test_prompt_sections_are_deterministic_versioned_and_role_scoped() -> None:
    context = PromptContext(
        role="knowledge",
        project_id="project-1",
        task_id="task-1",
        task_status="working",
        enabled_tools=("search_business_knowledge", "inspect_workspace"),
    )

    first = assemble_prompt(context)
    second = assemble_prompt(context)

    assert first is second
    assert first.section_ids == (
        "identity",
        "safety",
        "role.knowledge",
        "recovery",
        "output.handoff",
        "runtime",
    )
    assert all(section.version == 1 for section in first.sections)
    assert "Business Knowledge" in first.text
    assert "query_database" not in first.text
    assert "Enabled tools: inspect_workspace, search_business_knowledge" in first.text
    assert first.content_hash == second.content_hash


def test_lead_prompt_keeps_stable_policy_before_dynamic_runtime() -> None:
    assembly = assemble_prompt(
        PromptContext(role="lead", project_id="project-1", enabled_tools=("spawn_teammate",))
    )

    assert assembly.section_ids[:4] == ("identity", "safety", "role.lead", "recovery")
    assert assembly.sections[-1].stability == "dynamic"
    assert "Lead must not execute database SQL directly" in assembly.text
