from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import (
    ApprovalRecord,
    DraftDocument,
    EvidenceItem,
    Finding,
    PlanArtifact,
    ReflectionArtifact,
    ReviewReport,
)
from .ollama_client import OllamaClient
from .roles import Gatekeeper, Planner, Reviewer, Writer
from .tools import GitHubTools, LocalGitTools


class WorkflowEngine:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path
        self.owner: str | None = None
        self.repo: str | None = None
        self.base_branch = "main"
        self.github_token: str | None = None

        self.local_tools = LocalGitTools(repo_path)
        self.github_tools = GitHubTools(self.owner, self.repo, token=self.github_token)

        self.llm_logs: list[dict[str, Any]] = []
        self.llm = OllamaClient(logger=self._log_llm)

        self.reviewer = Reviewer(self.llm)
        self.planner = Planner(self.llm)
        self.writer = Writer(self.llm)
        self.gatekeeper = Gatekeeper(self.llm)

        self.pending_drafts: dict[str, dict[str, Any]] = {}
        self.reflections: list[dict[str, Any]] = []

        self.artifact_dir = repo_path / "artifacts"
        self.artifact_dir.mkdir(exist_ok=True)

    def _log_llm(self, record: dict[str, Any]) -> None:
        self.llm_logs.append(record)
        if len(self.llm_logs) > 250:
            self.llm_logs = self.llm_logs[-250:]

    def list_llm_logs(self) -> list[dict[str, Any]]:
        return list(self.llm_logs)

    def clear_llm_logs(self) -> None:
        self.llm_logs.clear()

    def configure(self, owner: str, repo: str, base_branch: str = "main", token: str | None = None) -> None:
        self.owner = owner.strip() or None
        self.repo = repo.strip() or None
        self.base_branch = base_branch.strip() or "main"
        if token is not None:
            self.github_token = token.strip() or None
        self.github_tools = GitHubTools(self.owner, self.repo, token=self.github_token)

    def set_token(self, token: str) -> None:
        self.github_token = token.strip() or None
        self.github_tools = GitHubTools(self.owner, self.repo, token=self.github_token)

    def list_accessible_repos(self) -> list[dict[str, Any]] | dict[str, Any]:
        repos = self.github_tools.list_user_repos()
        if isinstance(repos, dict):
            return repos

        simplified: list[dict[str, Any]] = []
        for repo in repos:
            owner_block = repo.get("owner") or {}
            simplified.append(
                {
                    "id": repo.get("id"),
                    "name": repo.get("name"),
                    "full_name": repo.get("full_name"),
                    "owner": owner_block.get("login"),
                    "description": repo.get("description") or "",
                    "private": bool(repo.get("private")),
                    "default_branch": repo.get("default_branch") or "main",
                    "updated_at": repo.get("updated_at") or "",
                    "html_url": repo.get("html_url") or "",
                }
            )

        return simplified

    def select_repo(self, owner: str, repo: str, base_branch: str | None = None) -> None:
        chosen_base = base_branch.strip() if base_branch else self.base_branch
        self.configure(owner=owner, repo=repo, base_branch=chosen_base or "main")

    def list_local_branches(self) -> list[str]:
        return self.local_tools.list_branches()

    def current_local_branch(self) -> str:
        return self.local_tools.current_branch()

    def list_repo_branches(self) -> list[str]:
        payload = self.github_tools.list_branches()
        if not isinstance(payload, list):
            return []
        names = [str(item.get("name", "")).strip() for item in payload]
        return [name for name in names if name]

    def _persist_artifact(self, prefix: str, artifact: dict[str, Any]) -> None:
        file_path = self.artifact_dir / f"{prefix}_{uuid4().hex[:8]}.json"
        file_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    def _plan(self, objective: str, scope: str) -> PlanArtifact:
        return PlanArtifact(
            objective=objective,
            scope=scope,
            required_tools=["git diff", "git status", "file reads", "GitHub API fetch"],
            success_checks=[
                "Evidence collected with IDs",
                "Action decision justified from evidence",
                "Reflection artifact generated",
            ],
            abort_conditions=[
                "No evidence available",
                "Reflection verdict FAIL for creation",
                "User rejects approval",
            ],
        )

    def _collect_review(self, diff: str, status: str, changed_files: list[str], remote_meta: dict[str, Any]) -> dict[str, Any]:
        file_contents = {f: self.local_tools.read_file(f) for f in changed_files[:5]}
        evidence = self.reviewer.gather_evidence(diff, status, changed_files, file_contents, remote_meta)
        analysis = self.reviewer.analyze(evidence)
        report = self.planner.decide(analysis, evidence)
        reflection = self.gatekeeper.reflect(report=report)
        return {"review": report, "reflection": reflection}

    def run_review_current_branch(self) -> dict[str, Any]:
        plan = self._plan("Review current branch changes", "current branch")
        status = self.local_tools.status()
        diff = self.local_tools.diff_current()
        changed_files = self.local_tools.changed_files()
        remote_meta = self.github_tools.repo_meta()

        review_bundle = self._collect_review(diff, status, changed_files, remote_meta)
        result = {
            "plan": plan.to_dict(),
            "review": review_bundle["review"].to_dict(),
            "reflection": review_bundle["reflection"].to_dict(),
        }
        self.reflections.append(result["reflection"])
        self._persist_artifact("review", result)
        return result

    def run_review_range(self, commit_range: str) -> dict[str, Any]:
        plan = self._plan("Review commit range changes", commit_range)
        status = self.local_tools.status()
        diff = self.local_tools.diff_range(commit_range)
        changed_files = self.local_tools.changed_files(commit_range)
        remote_meta = self.github_tools.repo_meta()

        review_bundle = self._collect_review(diff, status, changed_files, remote_meta)
        result = {
            "plan": plan.to_dict(),
            "review": review_bundle["review"].to_dict(),
            "reflection": review_bundle["reflection"].to_dict(),
        }
        self.reflections.append(result["reflection"])
        self._persist_artifact("review_range", result)
        return result

    def run_review_branch_compare(self, source_branch: str, target_branch: str) -> dict[str, Any]:
        source = source_branch.strip()
        target = target_branch.strip()
        scope = f"{target}...{source}"
        plan = self._plan("Review source branch against target branch", scope)

        compare_payload = self.github_tools.compare_branches(base=target, head=source)
        if compare_payload.get("error"):
            result = {
                "plan": plan.to_dict(),
                "review": {
                    "category": "chore",
                    "risk": "medium",
                    "decision": "no_action",
                    "justification": f"Remote compare failed: {compare_payload['error']}",
                    "confidence": 0.0,
                    "findings": [],
                    "evidence": [],
                },
                "reflection": {
                    "verdict": "FAIL",
                    "checks": {"remote_compare": "FAIL"},
                    "issues": [f"Remote compare failed: {compare_payload['error']}"]
                },
                "source_branch": source,
                "target_branch": target,
            }
            self._persist_artifact("review_branches_error", result)
            return result

        files = compare_payload.get("files") if isinstance(compare_payload.get("files"), list) else []
        changed_files = [str(item.get("filename", "")).strip() for item in files if str(item.get("filename", "")).strip()]
        diff = "\n\n".join(str(item.get("patch", "")) for item in files if str(item.get("patch", "")).strip())
        status = f"remote_compare {target}...{source}"
        remote_meta = self.github_tools.repo_meta()

        review_bundle = self._collect_review(diff, status, changed_files, remote_meta)
        result = {
            "plan": plan.to_dict(),
            "review": review_bundle["review"].to_dict(),
            "reflection": review_bundle["reflection"].to_dict(),
            "source_branch": source,
            "target_branch": target,
        }
        self.reflections.append(result["reflection"])
        self._persist_artifact("review_branches", result)
        return result

    def _enqueue_draft(self, draft: DraftDocument, reflection: ReflectionArtifact) -> dict[str, Any]:
        draft_id = uuid4().hex[:10]
        payload = {
            "draft_id": draft_id,
            "draft": draft.to_dict(),
            "reflection": reflection.to_dict(),
            "status": "awaiting_approval",
        }
        self.pending_drafts[draft_id] = payload
        self.reflections.append(reflection.to_dict())
        self._persist_artifact("draft", payload)
        return payload

    def draft_from_review(self, review_payload: dict[str, Any], draft_kind: str = "auto") -> dict[str, Any]:
        report = self._report_from_payload(review_payload)
        chosen = draft_kind
        if draft_kind == "auto":
            chosen = "pr" if report.decision == "create_pr" else "issue"

        draft = self.writer.draft_pr_from_review(report) if chosen == "pr" else self.writer.draft_issue_from_review(report)
        reflection = self.gatekeeper.reflect(report=report, draft=draft)
        return self._enqueue_draft(draft, reflection)

    def draft_from_instruction(self, kind: str, instruction: str, source_branch: str | None = None, target_branch: str | None = None) -> dict[str, Any]:
        plan = self._plan(f"Draft {kind} from explicit instruction", "user instruction")
        draft = self.writer.draft_from_instruction(kind, instruction, source_branch=source_branch, target_branch=target_branch)
        reflection = self.gatekeeper.reflect(draft=draft)
        payload = self._enqueue_draft(draft, reflection)
        payload["plan"] = plan.to_dict()
        self._persist_artifact("instruction_draft", payload)
        return payload

    def approve_draft(self, draft_id: str, approved: bool) -> dict[str, Any]:
        queued = self.pending_drafts.get(draft_id)
        if not queued:
            return {"error": "Draft not found."}

        if not approved:
            queued["status"] = "aborted"
            record = ApprovalRecord(draft_id=draft_id, status="rejected", reason="User rejected draft")
            self._persist_artifact("approval", record.to_dict())
            self.pending_drafts.pop(draft_id, None)
            return {"message": "Draft rejected. No changes made.", "record": record.to_dict()}

        reflection = ReflectionArtifact(**queued["reflection"])
        if not self.gatekeeper.approve_allowed(reflection):
            return {
                "message": "[Gatekeeper] Reflection verdict: FAIL. Revision required.",
                "reflection": queued["reflection"],
            }

        draft_data = dict(queued["draft"])
        draft_data.pop("markdown", None)
        draft = DraftDocument(**draft_data)

        created = self._create_github_artifact(draft)
        if created.get("error"):
            status = "create_failed"
            reason = created["error"]
        else:
            status = "created"
            reason = "GitHub artifact created successfully"

        record = ApprovalRecord(
            draft_id=draft_id,
            status=status,
            reason=reason,
            github_url=created.get("html_url"),
        )
        self._persist_artifact("approval", record.to_dict())
        self.pending_drafts.pop(draft_id, None)

        return {
            "message": "[Gatekeeper] Creating artifact completed.",
            "record": record.to_dict(),
            "github_response": created,
        }

    def _create_github_artifact(self, draft: DraftDocument) -> dict[str, Any]:
        body = draft.markdown()
        if draft.kind == "issue":
            return self.github_tools.create_issue(draft.title, body)

        head_branch = (draft.source_branch or "").strip() or self.local_tools.current_branch() or "HEAD"

        repo_meta = self.github_tools.repo_meta()
        default_branch = str(repo_meta.get("default_branch", "")).strip()

        branches_payload = self.github_tools.list_branches()
        available_branches: set[str] = set()
        if isinstance(branches_payload, list):
            available_branches = {
                str(item.get("name", "")).strip()
                for item in branches_payload
                if str(item.get("name", "")).strip()
            }

        preferred_target = (draft.target_branch or "").strip()
        candidates = [preferred_target, self.base_branch, default_branch, "main", "master"]
        candidate_bases: list[str] = []
        for branch in candidates:
            name = str(branch or "").strip()
            if not name or name in candidate_bases:
                continue
            if available_branches and name not in available_branches:
                continue
            candidate_bases.append(name)

        if not candidate_bases:
            if available_branches:
                candidate_bases = sorted(available_branches)
            elif default_branch:
                candidate_bases = [default_branch]
            elif self.base_branch:
                candidate_bases = [self.base_branch]

        last_error: dict[str, Any] | None = None
        for base in candidate_bases:
            created = self.github_tools.create_pr(draft.title, body, head=head_branch, base=base)
            if not created.get("error"):
                self.base_branch = base
                return created
            last_error = created

        if last_error is None:
            return {"error": "Unable to determine a valid base branch for PR creation."}

        error_text = str(last_error.get("error", ""))
        attempted = ", ".join(candidate_bases)
        base_error = re.search(r'"field"\s*:\s*"base"', error_text, re.IGNORECASE)
        if base_error:
            return {"error": f"{error_text} | attempted_base_branches=[{attempted}]"}
        return last_error

    def improve_existing(self, kind: str, number: int) -> dict[str, Any]:
        plan = self._plan(f"Improve existing {kind}", f"{kind} #{number}")
        payload = self.github_tools.fetch_issue(number) if kind == "issue" else self.github_tools.fetch_pr(number)
        improved = self.writer.improve_existing(kind, number, payload)

        placeholder_draft = self.writer.draft_from_instruction("issue", improved["improved_markdown"])
        reflection = self.gatekeeper.reflect(draft=placeholder_draft)

        result = {
            "plan": plan.to_dict(),
            "source": payload,
            "critique": improved["critique"],
            "improved_markdown": improved["improved_markdown"],
            "reflection": reflection.to_dict(),
        }
        self.reflections.append(result["reflection"])
        self._persist_artifact("improve", result)
        return result

    def _report_from_payload(self, payload: dict[str, Any]) -> ReviewReport:
        findings = [Finding(**f) if isinstance(f, dict) else f for f in payload.get("findings", [])]
        evidence = [EvidenceItem(**e) if isinstance(e, dict) else e for e in payload.get("evidence", [])]
        return ReviewReport(
            category=payload["category"],
            risk=payload["risk"],
            decision=payload["decision"],
            justification=payload["justification"],
            confidence=float(payload.get("confidence", 0.8)),
            findings=findings,
            evidence=evidence,
            created_at=payload.get("created_at", ""),
        )

    def list_pending_queue(self) -> list[dict[str, Any]]:
        return list(self.pending_drafts.values())

    def list_reflections(self) -> list[dict[str, Any]]:
        return list(self.reflections)




