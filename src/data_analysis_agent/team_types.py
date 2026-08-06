from enum import StrEnum


class AgentRole(StrEnum):
    KNOWLEDGE = "knowledge"
    SQL = "sql"
    REPORT = "report"
    CURATOR = "curator"
