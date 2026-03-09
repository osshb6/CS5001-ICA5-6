from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import DraftDocument, EvidenceItem, Finding, ReflectionArtifact, ReviewReport
from .ollama_client import OllamaClient


def _safe_float(value: Any, fallback: float = 0.7) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


class Reviewer:
    role_name = "Reviewer"

    def __init__(self, llm: OllamaClient | None = None) -> None:
        self.llm = llm or OllamaClient()

    def gather_evidence(
        self,
        diff_text: str,
        status_text: str,
        changed_files: list[str],
        file_contents: dict[str, str],
        remote_meta: dict[str, Any],
    ) -> list[EvidenceItem]:
        evidence: list[EvidenceItem] = [
            EvidenceItem("E1", "git_status", "Current status output", status_text or "(clean status)"),
            EvidenceItem("E2", "git_diff", "Diff under review", (diff_text or "(no diff)")[:12000]),
            EvidenceItem("E3", "changed_files", "List of changed files", "\n".join(changed_files) or "(none)"),
        ]
        idx = 4
        for path, content in file_contents.items():
            evidence.append(EvidenceItem(f"E{idx}", f"file:{path}", f"Snippet for {path}", (content or "(empty)")[:4000]))
            idx += 1

        if remote_meta.get("error"):
            evidence.append(
                EvidenceItem(f"E{idx}", "github_repo_meta_error", "Remote metadata fetch failed", remote_meta["error"])
            )
        else:
            evidence.append(
                EvidenceItem(
                    f"E{idx}",
                    "github_repo_meta",
                    "Repository metadata",
                    f"name={remote_meta.get('full_name')} default_branch={remote_meta.get('default_branch')}",
                )
            )
        return evidence

    def analyze(self, evidence: list[EvidenceItem]) -> dict[str, Any]:
        try:
            payload = self.llm.chat_json(
                system_prompt=(
                    "You are the Reviewer agent in a software workflow. "
                    "Analyze code evidence and return strict JSON only."
                ),
                user_prompt=(
                    "Return JSON with keys: category, risk, confidence, findings. "
                    "findings must be an array of objects {title, description, evidence_ids}. "
                    "risk must be one of low|medium|high.\n\n"
                    f"Evidence:\n{self._evidence_for_prompt(evidence)}"
                ),
                role=self.role_name,
                step="analyze",
            )
            findings = self._coerce_findings(payload.get("findings", []), evidence)
            return {
                "category": str(payload.get("category", "chore")).strip().lower() or "chore",
                "risk": self._normalize_risk(payload.get("risk")),
                "findings": findings,
                "confidence": _safe_float(payload.get("confidence"), 0.75),
                "changed_files": self._changed_files(evidence),
            }
        except Exception:
            return self._fallback_analyze(evidence)

    def _fallback_analyze(self, evidence: list[EvidenceItem]) -> dict[str, Any]:
        diff_content = next((e.content for e in evidence if e.source == "git_diff"), "")
        file_list = self._changed_files(evidence)

        category = "feature" if any(f.endswith((".py", ".ts", ".js", ".java", ".go", ".rb")) for f in file_list) else "chore"
        risk = "high" if len(diff_content.splitlines()) > 200 else "medium" if len(diff_content.splitlines()) > 40 else "low"

        findings: list[Finding] = []
        if "TODO" in diff_content or "FIXME" in diff_content:
            findings.append(
                Finding(
                    title="TODO/FIXME present in change",
                    description="The diff includes TODO/FIXME markers that may indicate incomplete work.",
                    evidence_ids=["E2"],
                )
            )

        code_files = [f for f in file_list if f.endswith((".py", ".ts", ".js", ".java", ".go", ".rb"))]
        test_files = [f for f in file_list if "test" in f.lower()]
        if code_files and not test_files:
            findings.append(
                Finding(
                    title="No tests changed",
                    description="Code files changed without corresponding tests in this diff.",
                    evidence_ids=["E3"],
                )
            )

        if not file_list:
            findings.append(
                Finding(
                    title="No file changes detected",
                    description="No changed files were detected in the selected scope.",
                    evidence_ids=["E3"],
                )
            )

        return {
            "category": category,
            "risk": risk,
            "findings": findings,
            "confidence": 0.65,
            "changed_files": file_list,
        }

    @staticmethod
    def _normalize_risk(value: Any) -> str:
        risk = str(value or "").strip().lower()
        return risk if risk in {"low", "medium", "high"} else "medium"

    @staticmethod
    def _changed_files(evidence: list[EvidenceItem]) -> list[str]:
        files = next((e.content for e in evidence if e.source == "changed_files"), "")
        return [line for line in files.splitlines() if line.strip()]

    @staticmethod
    def _coerce_findings(raw_findings: Any, evidence: list[EvidenceItem]) -> list[Finding]:
        valid_ids = {e.evidence_id for e in evidence}
        findings: list[Finding] = []
        for item in raw_findings if isinstance(raw_findings, list) else []:
            if not isinstance(item, dict):
                continue
            evidence_ids = [eid for eid in item.get("evidence_ids", []) if isinstance(eid, str) and eid in valid_ids]
            findings.append(
                Finding(
                    title=str(item.get("title", "Potential issue")).strip() or "Potential issue",
                    description=str(item.get("description", "Needs review.")).strip() or "Needs review.",
                    evidence_ids=evidence_ids or ["E2"],
                )
            )
        return findings

    @staticmethod
    def _evidence_for_prompt(evidence: list[EvidenceItem]) -> str:
        rows: list[str] = []
        for e in evidence:
            rows.append(f"{e.evidence_id} | {e.source} | {e.summary}\n{e.content[:1000]}")
        return "\n\n".join(rows)


class Planner:
    role_name = "Planner"

    def __init__(self, llm: OllamaClient | None = None) -> None:
        self.llm = llm or OllamaClient()

    def decide(self, analysis: dict[str, Any], evidence: list[EvidenceItem]) -> ReviewReport:
        findings: list[Finding] = analysis["findings"]
        category = str(analysis.get("category", "chore"))
        risk = str(analysis.get("risk", "medium"))
        confidence = _safe_float(analysis.get("confidence"), 0.75)

        try:
            payload = self.llm.chat_json(
                system_prompt="You are the Planner agent. Pick one action: create_issue, create_pr, or no_action.",
                user_prompt=(
                    "Return strict JSON with keys: decision, justification. "
                    "Decision must be one of create_issue|create_pr|no_action.\n\n"
                    f"Category: {category}\nRisk: {risk}\n"
                    f"Findings: {[asdict(f) for f in findings]}"
                ),
                role=self.role_name,
                step="decide",
            )
            decision = str(payload.get("decision", "no_action")).strip().lower()
            if decision not in {"create_issue", "create_pr", "no_action"}:
                decision = "no_action"
            justification = str(payload.get("justification", "Decision made from analysis.")).strip()
        except Exception:
            decision = self._fallback_decision(findings, risk)
            justification = f"Fallback planner decision based on findings_count={len(findings)} risk={risk}."

        if any("No file changes" in f.title for f in findings):
            decision = "no_action"

        return ReviewReport(
            category=category,
            risk=risk,
            decision=decision,
            justification=justification,
            confidence=confidence,
            findings=findings,
            evidence=evidence,
        )

    @staticmethod
    def _fallback_decision(findings: list[Finding], risk: str) -> str:
        if not findings:
            return "no_action"
        if any("No tests" in f.title for f in findings) or risk in {"medium", "high"}:
            return "create_pr"
        return "create_issue"


class Writer:
    role_name = "Writer"

    def __init__(self, llm: OllamaClient | None = None) -> None:
        self.llm = llm or OllamaClient()

    def draft_issue_from_review(self, report: ReviewReport) -> DraftDocument:
        evidence_ids = list(sorted({eid for f in report.findings for eid in f.evidence_ids})) or ["E2"]

        try:
            payload = self.llm.chat_json(
                system_prompt="You are the Writer agent. Draft a high-quality GitHub issue from a review report.",
                user_prompt=(
                    "Return strict JSON with keys: title, risk_level, sections. "
                    "sections must include Problem Description, Evidence, Acceptance Criteria.\n\n"
                    f"Report: {report.to_dict()}"
                ),
                role=self.role_name,
                step="draft_issue_from_review",
            )
            sections = self._coerce_sections(payload.get("sections", {}), issue=True)
            title = str(payload.get("title", f"Follow-up: {report.category} changes need hardening")).strip()
            risk = self._normalize_risk(payload.get("risk_level", report.risk))
        except Exception:
            title = f"Follow-up: {report.category} changes need hardening"
            risk = report.risk
            sections = {
                "Problem Description": "\n".join([f"- {f.description}" for f in report.findings]) or "No concrete problems identified.",
                "Evidence": f"Evidence references: {', '.join(evidence_ids)}",
                "Acceptance Criteria": "- Address all findings\n- Add or update relevant tests\n- Re-run review and reach PASS",
            }

        return DraftDocument(
            kind="issue",
            title=title,
            sections=sections,
            risk_level=risk,
            evidence_ids=evidence_ids,
            source="review",
        )

    def draft_pr_from_review(self, report: ReviewReport) -> DraftDocument:
        evidence_ids = list(sorted({eid for f in report.findings for eid in f.evidence_ids})) or ["E2", "E3"]

        try:
            payload = self.llm.chat_json(
                system_prompt="You are the Writer agent. Draft a high-quality GitHub pull request from a review report.",
                user_prompt=(
                    "Return strict JSON with keys: title, risk_level, sections. "
                    "sections must include Summary, Files Affected, Behavior Change, Test Plan.\n\n"
                    f"Report: {report.to_dict()}"
                ),
                role=self.role_name,
                step="draft_pr_from_review",
            )
            sections = self._coerce_sections(payload.get("sections", {}), issue=False)
            title = str(payload.get("title", f"Improve {report.category} change safety")).strip()
            risk = self._normalize_risk(payload.get("risk_level", report.risk))
        except Exception:
            affected = next((e.content for e in report.evidence if e.source == "changed_files"), "(none)")
            title = f"Improve {report.category} change safety"
            risk = report.risk
            sections = {
                "Summary": "This PR addresses follow-up improvements detected during automated review.",
                "Files Affected": affected,
                "Behavior Change": "Improves maintainability and reduces regression risk based on review findings.",
                "Test Plan": "- Add/adjust tests for changed logic\n- Run unit tests and smoke checks",
            }

        return DraftDocument(
            kind="pr",
            title=title,
            sections=sections,
            risk_level=risk,
            evidence_ids=evidence_ids,
            source="review",
        )

    def draft_from_instruction(self, kind: str, instruction: str, source_branch: str | None = None, target_branch: str | None = None) -> DraftDocument:
        kind = "issue" if kind not in {"issue", "pr"} else kind
        try:
            payload = self.llm.chat_json(
                system_prompt="You are the Writer agent. Draft a GitHub issue or PR from user instruction.",
                user_prompt=(
                    "Return strict JSON with keys: title, risk_level, sections. "
                    "If kind=issue include Problem Description, Evidence, Acceptance Criteria. "
                    "If kind=pr include Summary, Files Affected, Behavior Change, Test Plan.\n\n"
                    f"kind={kind}\ninstruction={instruction}\nsource_branch={source_branch}\ntarget_branch={target_branch}"
                ),
                role=self.role_name,
                step="draft_from_instruction",
            )
            sections = self._coerce_sections(payload.get("sections", {}), issue=(kind == "issue"))
            title = str(payload.get("title", f"{kind.title()} Draft: {instruction[:60]}"))
            risk = self._normalize_risk(payload.get("risk_level", "medium"))
        except Exception:
            title = f"{kind.title()} Draft: {instruction[:60]}"
            risk = "medium"
            if kind == "issue":
                sections = {
                    "Problem Description": instruction,
                    "Evidence": "Provided by explicit user instruction.",
                    "Acceptance Criteria": "- Clarify expected behavior\n- Implement and validate with tests",
                }
            else:
                sections = {
                    "Summary": instruction,
                    "Files Affected": "To be finalized during implementation.",
                    "Behavior Change": "Expected to improve code quality according to instruction.",
                    "Test Plan": "- Add or update tests for changed behavior\n- Run relevant suite",
                }

        return DraftDocument(
            kind=kind,
            title=title,
            sections=sections,
            risk_level=risk,
            evidence_ids=["USER_INSTRUCTION"],
            source="instruction",
            source_branch=source_branch if kind == "pr" else None,
            target_branch=target_branch if kind == "pr" else None,
        )

    def improve_existing(self, kind: str, number: int, payload: dict[str, Any]) -> dict[str, Any]:
        title = payload.get("title", f"{kind.title()} #{number}")
        body = payload.get("body", "") or ""

        try:
            llm_payload = self.llm.chat_json(
                system_prompt="You are the Writer agent improving existing GitHub artifacts.",
                user_prompt=(
                    "Return strict JSON with keys: critique (array of strings), improved_markdown (string).\n\n"
                    f"kind={kind}\nnumber={number}\ntitle={title}\nbody={body}"
                ),
                role=self.role_name,
                step="improve_existing",
            )
            critique = [str(item) for item in llm_payload.get("critique", []) if str(item).strip()]
            improved_markdown = str(llm_payload.get("improved_markdown", "")).strip()
            if critique and improved_markdown:
                return {"critique": critique, "improved_markdown": improved_markdown}
        except Exception:
            pass

        critique: list[str] = []
        if len(body.strip()) < 80:
            critique.append("Description is too brief and likely lacks implementation detail.")
        if "acceptance" not in body.lower() and "criteria" not in body.lower():
            critique.append("Acceptance criteria are missing or unclear.")
        if "test" not in body.lower():
            critique.append("Test expectations are not explicit.")
        if not critique:
            critique.append("Structure is acceptable but can be made more explicit and measurable.")

        improved = (
            f"# {title}\n\n"
            "## Context\n"
            f"{body or 'Current description was sparse; context inferred from existing content.'}\n\n"
            "## Gaps Identified\n"
            + "\n".join([f"- {line}" for line in critique])
            + "\n\n## Proposed Acceptance Criteria\n"
            "- Behavior is clearly defined for normal and edge cases\n"
            "- Automated test coverage is specified\n"
            "- Scope and non-goals are explicit\n"
            "\n## Risk Level\n"
            "- medium"
        )

        return {"critique": critique, "improved_markdown": improved}

    @staticmethod
    def _normalize_risk(value: Any) -> str:
        risk = str(value or "").strip().lower()
        return risk if risk in {"low", "medium", "high"} else "medium"

    @staticmethod
    def _coerce_sections(raw: Any, issue: bool) -> dict[str, str]:
        required = ["Problem Description", "Evidence", "Acceptance Criteria"] if issue else [
            "Summary",
            "Files Affected",
            "Behavior Change",
            "Test Plan",
        ]
        sections: dict[str, str] = {}
        if isinstance(raw, dict):
            for key in required:
                value = raw.get(key, "")
                sections[key] = str(value).strip() if value is not None else ""
        for key in required:
            sections[key] = sections.get(key) or "TBD"
        return sections


class Gatekeeper:
    role_name = "Gatekeeper"

    def __init__(self, llm: OllamaClient | None = None) -> None:
        self.llm = llm or OllamaClient()

    def reflect(
        self,
        report: ReviewReport | None = None,
        draft: DraftDocument | None = None,
        approved: bool | None = None,
    ) -> ReflectionArtifact:
        issues: list[str] = []
        checks: dict[str, str] = {}

        if report:
            known_evidence_ids = {e.evidence_id for e in report.evidence}
            unsupported = [
                f.title
                for f in report.findings
                if not f.evidence_ids or not set(f.evidence_ids).issubset(known_evidence_ids)
            ]
            checks["unsupported_claims"] = "FAIL" if unsupported else "PASS"
            if unsupported:
                issues.append(f"Unsupported claims found: {', '.join(unsupported)}")

            missing_evidence = not bool(report.evidence)
            checks["missing_evidence"] = "FAIL" if missing_evidence else "PASS"
            if missing_evidence:
                issues.append("No evidence collected.")

        if draft:
            has_test_plan = "Test Plan" in draft.sections and bool(draft.sections["Test Plan"].strip())
            if draft.kind == "pr":
                checks["missing_tests"] = "PASS" if has_test_plan else "FAIL"
                if not has_test_plan:
                    issues.append("PR draft missing test plan.")
            else:
                checks["missing_tests"] = "PASS"

        if approved is None:
            checks["policy_violations"] = "PASS"
        else:
            policy_fail = approved and draft is None
            checks["policy_violations"] = "FAIL" if policy_fail else "PASS"
            if policy_fail:
                issues.append("Creation attempted without a draft.")

        llm_checks, llm_issues = self._llm_safety_audit(report, draft)
        checks.update(llm_checks)
        issues.extend(llm_issues)

        verdict = "FAIL" if any(v == "FAIL" for v in checks.values()) else "PASS"
        return ReflectionArtifact(verdict=verdict, checks=checks, issues=issues)

    def _llm_safety_audit(
        self,
        report: ReviewReport | None,
        draft: DraftDocument | None,
    ) -> tuple[dict[str, str], list[str]]:
        try:
            payload = self.llm.chat_json(
                system_prompt="You are the Gatekeeper agent. Evaluate safety and policy compliance.",
                user_prompt=(
                    "Return strict JSON with keys: checks (object of PASS/FAIL), issues (array of strings).\n\n"
                    f"report={report.to_dict() if report else None}\n"
                    f"draft={draft.to_dict() if draft else None}"
                ),
                role=self.role_name,
                step="safety_audit",
            )
            raw_checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
            checks: dict[str, str] = {}
            for key, value in raw_checks.items():
                normalized = str(value).strip().upper()
                checks[str(key)] = "FAIL" if normalized == "FAIL" else "PASS"
            issues = [str(item).strip() for item in payload.get("issues", []) if str(item).strip()]
            return checks, issues
        except Exception:
            return {}, []

    @staticmethod
    def approve_allowed(reflection: ReflectionArtifact) -> bool:
        return reflection.verdict == "PASS"



