"""Strict contracts shared by evidence tools, the claim validator and reports."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue

Language = Literal["en", "ne"]
Layer = Literal["buildings", "roads", "bridges", "settlements", "destinations"]
SubjectType = Literal["analysis", "observation", "buildings", "roads", "bridges", "settlements", "destinations"]
Interpretation = Literal["measurement", "potential_exposure", "modeled_connectivity", "recorded_metadata", "limitation", "unavailable"]
Topic = Literal["summary", "flood", "infrastructure", "connectivity", "sources", "limitations", "feature"]
ExplanationCode = Literal["potential_damage", "modeled_closure", "coverage_limits", "surface_change_uncertain", "satellite_limits", "routing_limits", "missing_data"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class EvidenceFact(StrictModel):
    fact_id: str
    run_id: str
    field_name: str
    path: str
    subject_type: SubjectType
    subject_id: str
    subject_name: str | None
    value: JsonValue
    unit: str | None
    status: Literal["available", "missing", "invalid"]
    label_en: str
    label_ne: str
    source_ids: list[str]
    method: str
    timestamp: str | None
    interpretation: Interpretation
    uncertainty: str | None
    missing_reason: str | None


class ToolResult(StrictModel):
    run_id: str
    tool: str
    status: Literal["ok", "partial", "not_found"]
    facts: list[EvidenceFact]
    feature_ids: list[str] = Field(default_factory=list)
    next_offset: int | None = None
    missing: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class EmptyArgs(StrictModel):
    pass


class InfrastructureArgs(StrictModel):
    asset_type: Literal["buildings", "roads", "bridges"]
    only_exposed: bool
    offset: int = Field(ge=0, le=100000)
    limit: int = Field(ge=1, le=25)
    query: str | None = Field(default=None, min_length=1, max_length=200)


class ConnectivityArgs(StrictModel):
    status: Literal["all", "cut_off", "connected", "uncertain_access", "unassessed", "baseline_disconnected"]
    offset: int = Field(ge=0, le=100000)
    limit: int = Field(ge=1, le=25)
    query: str | None = Field(default=None, min_length=1, max_length=200)
    road_id: str | None = Field(default=None, min_length=1, max_length=200)


class FeatureArgs(StrictModel):
    layer: Layer
    feature_id: str = Field(min_length=1, max_length=200)


class Claim(StrictModel):
    fact_id: str
    run_id: str
    subject_type: SubjectType
    subject_id: str
    field_name: str
    interpretation: Interpretation
    operation: Literal["exact", "round_1", "round_2", "round_3"]


class AnswerSection(StrictModel):
    topic: Topic
    claims: list[Claim] = Field(max_length=80)
    explanations: list[ExplanationCode] = Field(max_length=7)


class AnswerDraft(StrictModel):
    run_id: str
    language: Language
    kind: Literal["answer", "report", "unsupported", "clarification"]
    sections: list[AnswerSection] = Field(max_length=8)


class GenerationInfo(StrictModel):
    mode: Literal["llm", "deterministic_fallback"]
    provider: str | None
    model: str | None
    reason: str | None
    validation: Literal["validated_claims", "deterministic_facts"]


class ReportSection(StrictModel):
    heading: str
    lines: list[str]
    fact_ids: list[str]


class SituationReport(StrictModel):
    report_id: str
    run_id: str
    language: Language
    title: str
    generated_at: str
    analysis_name: str
    event_date: str
    data_mode: Literal["demo", "live"]
    status: Literal["complete", "partial"]
    sections: list[ReportSection]
    facts: list[EvidenceFact]
    attributions: list[str]
    generation: GenerationInfo
    missing_fields: list[str]
