from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any

import requests


class LocalGitTools:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path

    def _run(self, args: list[str]) -> str:
        cmd = ["git", "-c", f"safe.directory={self.repo_path.as_posix()}", "-C", str(self.repo_path), *args]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return proc.stdout.strip() if proc.returncode == 0 else proc.stderr.strip()

    def status(self) -> str:
        return self._run(["status", "--short"])

    def current_branch(self) -> str:
        return self._run(["rev-parse", "--abbrev-ref", "HEAD"]) or "HEAD"

    def list_branches(self) -> list[str]:
        local_output = self._run(["for-each-ref", "--format=%(refname:short)", "refs/heads"])
        remote_output = self._run(["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/*"])

        raw = [line.strip() for line in (local_output + "\n" + remote_output).splitlines() if line.strip()]
        deduped: list[str] = []
        for branch in raw:
            cleaned = branch
            if cleaned in {"origin", "origin/HEAD", "HEAD"}:
                continue
            if cleaned.startswith("origin/"):
                cleaned = cleaned[len("origin/") :]
            if not cleaned or cleaned == "HEAD":
                continue
            if cleaned not in deduped:
                deduped.append(cleaned)
        return deduped

    def diff_current(self) -> str:
        return self._run(["diff", "--no-color"])

    def diff_range(self, commit_range: str) -> str:
        return self._run(["diff", "--no-color", commit_range])

    def diff_branches(self, source_branch: str, target_branch: str) -> str:
        spec = f"{target_branch}...{source_branch}"
        return self._run(["diff", "--no-color", spec])

    def changed_files(self, commit_range: str | None = None) -> list[str]:
        args = ["diff", "--name-only"]
        if commit_range:
            args.append(commit_range)
        output = self._run(args)
        return [line for line in output.splitlines() if line.strip()]

    def changed_files_branches(self, source_branch: str, target_branch: str) -> list[str]:
        spec = f"{target_branch}...{source_branch}"
        output = self._run(["diff", "--name-only", spec])
        return [line for line in output.splitlines() if line.strip()]

    def read_file(self, rel_path: str, max_chars: int = 4000) -> str:
        path = self.repo_path / rel_path
        if not path.exists() or not path.is_file():
            return ""
        content = path.read_text(encoding="utf-8", errors="ignore")
        return content[:max_chars]


class GitHubTools:
    def __init__(self, owner: str | None, repo: str | None, token: str | None = None) -> None:
        self.owner = owner
        self.repo = repo
        self.token = token or os.getenv("GITHUB_TOKEN", "")

    @property
    def enabled(self) -> bool:
        return bool(self.owner and self.repo and self.token)

    @property
    def authenticated(self) -> bool:
        return bool(self.token)

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        if not self.enabled:
            return {"error": "GitHub integration not configured (owner/repo/token required)."}
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}{endpoint}"
        response = requests.request(method, url, headers=self._auth_headers(), json=payload, timeout=20)
        if response.status_code >= 400:
            return {"error": f"{response.status_code}: {response.text}"}
        return response.json()

    def authenticated_user(self) -> dict[str, Any]:
        if not self.authenticated:
            return {"error": "GitHub token required."}
        response = requests.get("https://api.github.com/user", headers=self._auth_headers(), timeout=20)
        if response.status_code >= 400:
            return {"error": f"{response.status_code}: {response.text}"}
        return response.json()

    def list_user_repos(self) -> list[dict[str, Any]] | dict[str, Any]:
        if not self.authenticated:
            return {"error": "GitHub token required."}

        all_repos: list[dict[str, Any]] = []
        page = 1
        while True:
            response = requests.get(
                "https://api.github.com/user/repos",
                headers=self._auth_headers(),
                params={
                    "per_page": 100,
                    "page": page,
                    "sort": "updated",
                    "affiliation": "owner,collaborator,organization_member",
                },
                timeout=20,
            )
            if response.status_code >= 400:
                return {"error": f"{response.status_code}: {response.text}"}

            chunk = response.json()
            if not chunk:
                break
            all_repos.extend(chunk)

            if len(chunk) < 100:
                break
            page += 1

        return all_repos

    def repo_meta(self) -> dict[str, Any]:
        result = self._request("GET", "")
        return result if isinstance(result, dict) else {"error": "Unexpected GitHub response."}

    def fetch_issue(self, number: int) -> dict[str, Any]:
        result = self._request("GET", f"/issues/{number}")
        return result if isinstance(result, dict) else {"error": "Unexpected GitHub response."}

    def fetch_pr(self, number: int) -> dict[str, Any]:
        result = self._request("GET", f"/pulls/{number}")
        return result if isinstance(result, dict) else {"error": "Unexpected GitHub response."}

    def list_branches(self) -> list[dict[str, Any]] | dict[str, Any]:
        result = self._request("GET", "/branches")
        if isinstance(result, list):
            return result
        return result

    def compare_branches(self, base: str, head: str) -> dict[str, Any]:
        result = self._request("GET", f"/compare/{base}...{head}")
        return result if isinstance(result, dict) else {"error": "Unexpected GitHub response."}

    def create_issue(self, title: str, body: str) -> dict[str, Any]:
        result = self._request("POST", "/issues", {"title": title, "body": body})
        return result if isinstance(result, dict) else {"error": "Unexpected GitHub response."}

    def create_pr(self, title: str, body: str, head: str, base: str) -> dict[str, Any]:
        payload = {"title": title, "body": body, "head": head, "base": base}
        result = self._request("POST", "/pulls", payload)
        return result if isinstance(result, dict) else {"error": "Unexpected GitHub response."}



