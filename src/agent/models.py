from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EvidenceItem:
    evidence_id: str
    source: str
    summary: str
    content: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanArtifact:
    objective: str
    scope: str
    required_tools: list[str]
    success_checks: list[str]
    abort_conditions: list[str]
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Finding:
    title: str
    description: str
    evidence_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewReport:
    category: str
    risk: str
    decision: str
    justification: str
    confidence: float
    findings: list[Finding]
    evidence: list[EvidenceItem]
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["findings"] = [f.to_dict() for f in self.findings]
        data["evidence"] = [e.to_dict() for e in self.evidence]
        return data


@dataclass
class DraftDocument:
    kind: str
    title: str
    sections: dict[str, str]
    risk_level: str
    evidence_ids: list[str]
    source: str
    source_branch: str | None = None
    target_branch: str | None = None
    created_at: str = field(default_factory=utc_now_iso)

    def markdown(self) -> str:
        lines = [f"# {self.title}", ""]
        for k, v in self.sections.items():
            lines.append(f"## {k}")
            lines.append(v)
            lines.append("")
        lines.append(f"Risk level: **{self.risk_level}**")
        return "\n".join(lines).strip()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["markdown"] = self.markdown()
        return data


@dataclass
class ReflectionArtifact:
    verdict: str
    checks: dict[str, str]
    issues: list[str]
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApprovalRecord:
    draft_id: str
    status: str
    reason: str
    created_at: str = field(default_factory=utc_now_iso)
    github_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


