from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from src.models import OrganizationConstraint, OrganizationState


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
ORGANIZATION_FILE = DATA_DIR / "organization_state.json"


def load_organization_state() -> OrganizationState:
    if not ORGANIZATION_FILE.exists():
        return OrganizationState()
    return OrganizationState.model_validate_json(
        ORGANIZATION_FILE.read_text(encoding="utf-8")
    )


def save_organization_state(state: OrganizationState) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_file = DATA_DIR / f"organization_state_{uuid4().hex}.tmp"
    temporary_file.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    temporary_file.replace(ORGANIZATION_FILE)


def constraints_for_agent(
    state: OrganizationState,
    agent_name: str,
) -> list[OrganizationConstraint]:
    return [
        item
        for item in state.constraints
        if item.active and item.agent_name in {None, agent_name}
    ]


def add_constraint(
    state: OrganizationState,
    *,
    rule: str,
    source_case_id: str,
    source_summary: str,
    agent_name: str | None = None,
) -> OrganizationConstraint:
    normalized_rule = rule.strip()
    for existing in state.constraints:
        if (
            existing.active
            and existing.agent_name == agent_name
            and existing.rule.strip() == normalized_rule
        ):
            return existing

    constraint = OrganizationConstraint(
        agent_name=agent_name,
        rule=normalized_rule,
        source_case_id=source_case_id,
        source_summary=source_summary,
    )
    state.constraints.append(constraint)
    return constraint

