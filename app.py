from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import streamlit as st

from src.agent.workflow import WorkflowEngine


st.set_page_config(page_title="Personalized GitHub Repository Agent", layout="wide")
st.title("Personalized GitHub Repository Agent")


def get_engine() -> WorkflowEngine:
    if "engine" not in st.session_state:
        st.session_state.engine = WorkflowEngine(repo_path=Path.cwd())
    return st.session_state.engine


def load_repos(engine: WorkflowEngine, token: str) -> None:
    engine.set_token(token)
    repos = engine.list_accessible_repos()
    if isinstance(repos, dict):
        st.session_state.repo_load_error = repos.get("error", "Failed to load repositories.")
        st.session_state.repos = []
        st.session_state.repo_user = None
        return

    user = engine.github_tools.authenticated_user()
    st.session_state.repo_user = None if isinstance(user, dict) and user.get("error") else user.get("login")
    st.session_state.repos = repos
    st.session_state.repo_load_error = None


def refresh_repo_branches(engine: WorkflowEngine) -> None:
    branches = engine.list_repo_branches()
    st.session_state.remote_branches = branches
    if branches:
        st.session_state.source_branch = st.session_state.get("source_branch") or branches[0]
        default_target = st.session_state.get("target_branch") or engine.base_branch
        st.session_state.target_branch = default_target if default_target in branches else branches[0]


def decision_label(value: str) -> str:
    normalized = value.strip().lower()
    return {
        "create_issue": "Create Issue",
        "create_pr": "Create PR",
        "no_action": "No action required",
        "no_action_required": "No action required",
    }.get(normalized, value or "No action required")


def render_reflection(reflection: dict[str, Any], heading: str = "Reflection") -> None:
    with st.container(border=True):
        st.markdown(f"**{heading}**")

        verdict = str(reflection.get("verdict", "unknown"))
        if verdict.upper() == "PASS":
            st.success(f"Verdict: {verdict}")
        elif verdict.upper() == "FAIL":
            st.error(f"Verdict: {verdict}")
        else:
            st.info(f"Verdict: {verdict}")

        checks = reflection.get("checks") or {}
        if checks:
            st.markdown("Checks")
            for key, value in checks.items():
                st.write(f"- {key}: {value}")

        issues = reflection.get("issues") or []
        st.markdown("Issues")
        if issues:
            for issue in issues:
                st.write(f"- {issue}")
        else:
            st.write("- None")

        created_at = reflection.get("created_at")
        if created_at:
            st.caption(f"Created: {created_at}")


def render_plan(plan: dict[str, Any]) -> None:
    with st.container(border=True):
        st.markdown("**Plan Summary**")
        st.write(f"Objective: {plan.get('objective', 'n/a')}")
        st.write(f"Scope: {plan.get('scope', 'n/a')}")

        required_tools = plan.get("required_tools") or []
        if required_tools:
            st.markdown("Required tools")
            for tool in required_tools:
                st.write(f"- {tool}")


def render_draft_payload(payload: dict[str, Any], heading: str = "Draft") -> None:
    draft = payload.get("draft") or {}
    sections = draft.get("sections") or {}

    with st.container(border=True):
        st.markdown(f"**{heading}**")
        st.write(f"Draft ID: {payload.get('draft_id', 'n/a')}")
        st.write(f"Status: {payload.get('status', 'n/a')}")
        st.write(f"Type: {draft.get('kind', 'n/a')}")
        st.write(f"Title: {draft.get('title', 'n/a')}")
        st.write(f"Risk level: {draft.get('risk_level', 'n/a')}")

        evidence_ids = draft.get("evidence_ids") or []
        if evidence_ids:
            st.write(f"Evidence IDs: {', '.join(evidence_ids)}")

        if sections:
            st.markdown("Sections")
            for section_name, section_content in sections.items():
                with st.expander(section_name):
                    st.write(section_content)

        markdown_preview = draft.get("markdown")
        if markdown_preview:
            with st.expander("Full Draft Markdown"):
                st.markdown(markdown_preview)



GATEKEEPER_FAIL_MESSAGE = "[Gatekeeper] Reflection verdict: FAIL. Revision required."


if hasattr(st, "dialog"):
    @st.dialog("Approval Blocked")
    def show_gatekeeper_fail_dialog(message: str) -> None:
        st.warning(f"Are you sure?\n\n{message}")
        st.caption("The Gatekeeper blocked creation due to failed safety checks.")
        if st.button("Close", key="close_gatekeeper_fail_dialog"):
            st.session_state.dismissed_gatekeeper_dialog = message
            st.rerun()
else:
    def show_gatekeeper_fail_dialog(message: str) -> None:
        st.error(f"Are you sure? {message}")
engine = get_engine()

if "remote_branches" not in st.session_state:
    refresh_repo_branches(engine)

with st.sidebar:
    st.header("GitHub Access")

    default_token = st.session_state.get("github_token") or os.getenv("GITHUB_TOKEN", "")
    token = st.text_input("GitHub Token", value=default_token, type="password")
    if st.button("Load My Repositories", type="primary"):
        if not token.strip():
            st.error("Enter a GitHub token first.")
        else:
            st.session_state.github_token = token.strip()
            load_repos(engine, token.strip())
            if st.session_state.get("repo_load_error"):
                st.error(st.session_state["repo_load_error"])
            else:
                st.success("Repositories loaded.")

    st.divider()
    st.header("Task Defaults")
    base_branch_input = st.text_input("Default Base Branch", value=engine.base_branch)
    if st.button("Apply Base Branch"):
        engine.base_branch = base_branch_input.strip() or "main"
        st.success("Default base branch updated.")

    st.divider()
    st.header("LLM Logs")
    if st.button("Clear LLM Logs"):
        engine.clear_llm_logs()
        st.success("LLM logs cleared.")

    logs = engine.list_llm_logs()
    st.caption(f"Total logs: {len(logs)}")
    for idx, log in enumerate(reversed(logs[-20:]), start=1):
        label = f"{log.get('role', 'Agent')} - {log.get('step', 'step')}"
        with st.expander(label):
            st.caption(str(log.get("timestamp", "")))
            if log.get("model"):
                st.write(f"Model: {log.get('model')}")
            if log.get("system_prompt"):
                st.text_area("System", value=str(log.get("system_prompt")), height=90, key=f"sys_{idx}")
            if log.get("user_prompt"):
                st.text_area("User", value=str(log.get("user_prompt")), height=140, key=f"usr_{idx}")
            if log.get("response"):
                st.text_area("Response", value=str(log.get("response")), height=140, key=f"res_{idx}")
            if log.get("response_json"):
                st.write(log.get("response_json"))
            if log.get("error"):
                st.error(str(log.get("error")))

repos = st.session_state.get("repos", [])
repo_error = st.session_state.get("repo_load_error")

st.subheader("Select Repository")
if repo_error:
    st.error(repo_error)
elif not repos:
    st.info("Load repositories with your GitHub token from the sidebar.")
else:
    user_login = st.session_state.get("repo_user")
    if user_login:
        st.caption(f"Showing repositories for `{user_login}`")

    cols_per_row = 3
    for i in range(0, len(repos), cols_per_row):
        row = repos[i : i + cols_per_row]
        cols = st.columns(cols_per_row)
        for idx, repo in enumerate(row):
            with cols[idx]:
                with st.container(border=True):
                    st.markdown(f"### {repo['name']}")
                    st.caption(repo["full_name"])
                    st.write(repo.get("description") or "No description.")

                    visibility = "Private" if repo.get("private") else "Public"
                    st.caption(f"{visibility} | Default branch: `{repo.get('default_branch', 'main')}`")
                    if st.button("Select Repo", key=f"select_{repo['id']}", use_container_width=True):
                        engine.select_repo(
                            owner=repo.get("owner", ""),
                            repo=repo.get("name", ""),
                            base_branch=repo.get("default_branch", "main"),
                        )
                        refresh_repo_branches(engine)
                        st.success(f"Selected `{repo['full_name']}`")

if engine.owner and engine.repo:
    st.info(f"Active repository: `{engine.owner}/{engine.repo}` | Base branch: `{engine.base_branch}`")
else:
    st.warning("No repository selected yet. Select one card to run tasks against it.")


tabs = st.tabs(["Review", "Draft", "Improve", "Approval Queue"])

with tabs[0]:
    st.subheader("Task 1: Review Changes")

    branches = st.session_state.get("remote_branches", [])
    col_a, col_b = st.columns([1, 1])
    with col_a:
        mode = st.radio("Review mode", ["Current branch", "Commit range", "Branch comparison"], horizontal=True)
    with col_b:
        if st.button("Refresh Remote Branches"):
            refresh_repo_branches(engine)
            st.success("Remote branch list refreshed from selected repo.")

    commit_range = st.text_input(
        "Commit range",
        value="HEAD~3..HEAD",
        disabled=mode != "Commit range",
        help="Example: HEAD~3..HEAD",
    )

    default_source = st.session_state.get("source_branch") or (branches[0] if branches else "")
    default_target = st.session_state.get("target_branch") or (engine.base_branch if branches else "")

    source_branch = st.selectbox(
        "Source branch",
        options=branches or ["(no branches found)"],
        index=(branches.index(default_source) if branches and default_source in branches else 0),
        disabled=mode != "Branch comparison" or not bool(branches),
    )
    target_branch = st.selectbox(
        "Target branch",
        options=branches or ["(no branches found)"],
        index=(branches.index(default_target) if branches and default_target in branches else 0),
        disabled=mode != "Branch comparison" or not bool(branches),
    )

    if mode == "Branch comparison" and branches:
        st.caption(f"Analyzing remote diff: `{target_branch}...{source_branch}`")

    if st.button("Run Review", type="primary"):
        if not (engine.owner and engine.repo):
            st.error("Select a repository card first.")
        elif mode == "Current branch":
            result = engine.run_review_current_branch()
            st.session_state.last_review = result
            st.rerun()
        elif mode == "Commit range":
            result = engine.run_review_range(commit_range)
            st.session_state.last_review = result
            st.rerun()
        else:
            if not branches:
                st.error("No remote branches available. Refresh remote branches and try again.")
            elif source_branch == target_branch:
                st.error("Source and target branches must be different.")
            else:
                st.session_state.source_branch = source_branch
                st.session_state.target_branch = target_branch
                result = engine.run_review_branch_compare(source_branch=source_branch, target_branch=target_branch)
                st.session_state.last_review = result
                st.rerun()

    review_result = st.session_state.get("last_review")
    if review_result:
        review_data = review_result.get("review", {})
        findings = review_data.get("findings", [])
        if findings:
            issues_text = "\n".join(
                f"- {item.get('title', 'Issue')}: {item.get('description', '')}".strip()
                for item in findings
            )
        else:
            issues_text = "No significant issues or improvements identified."

        risk_value = str(review_data.get("risk", "unknown")).strip().lower()
        category_value = str(review_data.get("category", "unknown")).strip()
        decided = decision_label(str(review_data.get("decision", "no_action")))

        st.markdown("### Review Summary")
        col_left, col_right = st.columns(2)

        with col_left:
            with st.container(border=True):
                st.markdown("**1) Identify potential issues or improvements**")
                st.write(issues_text)
            with st.container(border=True):
                st.markdown("**2) Categorize change (feature, bugfix, refactor, etc.)**")
                st.write(category_value or "unknown")

        with col_right:
            with st.container(border=True):
                st.markdown("**3) Assess risk (low / medium / high)**")
                if risk_value == "high":
                    st.error(f"Risk: {risk_value}")
                elif risk_value == "medium":
                    st.warning(f"Risk: {risk_value}")
                elif risk_value == "low":
                    st.success(f"Risk: {risk_value}")
                else:
                    st.info(f"Risk: {risk_value or 'unknown'}")
            with st.container(border=True):
                st.markdown("**4) Decide**")
                if decided == "Create PR":
                    st.success(f"Decide: {decided}")
                elif decided == "Create Issue":
                    st.warning(f"Decide: {decided}")
                else:
                    st.info(f"Decide: {decided}")

with tabs[1]:
    st.subheader("Task 2: Draft Issue/PR")
    source = st.radio("Draft source", ["From Review", "Explicit Instruction"], horizontal=True)

    if source == "From Review":
        draft_kind = st.selectbox("Draft type", ["auto", "issue", "pr"])
        if st.button("Draft from last review"):
            if not (engine.owner and engine.repo):
                st.error("Select a repository card first.")
            else:
                review_result = st.session_state.get("last_review")
                if not review_result:
                    st.error("Run a review first.")
                else:
                    queued = engine.draft_from_review(review_result["review"], draft_kind=draft_kind)
                    st.session_state.last_draft = queued
                    st.rerun()
    else:
        instruction_kind = st.selectbox("Instruction type", ["issue", "pr"])

        if st.button("Refresh Remote Branches", key="refresh_remote_branches_draft"):
            refresh_repo_branches(engine)
            st.success("Remote branch list refreshed from selected repo.")

        draft_branches = st.session_state.get("remote_branches", [])
        draft_default_source = st.session_state.get("source_branch") or (draft_branches[0] if draft_branches else "")
        draft_default_target = st.session_state.get("target_branch") or (engine.base_branch if draft_branches else "")

        pr_source_branch = None
        pr_target_branch = None
        if instruction_kind == "pr":
            c1, c2 = st.columns(2)
            with c1:
                pr_source_branch = st.selectbox(
                    "PR Source branch",
                    options=draft_branches or ["(no branches found)"],
                    index=(draft_branches.index(draft_default_source) if draft_branches and draft_default_source in draft_branches else 0),
                    disabled=not bool(draft_branches),
                )
            with c2:
                pr_target_branch = st.selectbox(
                    "PR Target branch",
                    options=draft_branches or ["(no branches found)"],
                    index=(draft_branches.index(draft_default_target) if draft_branches and draft_default_target in draft_branches else 0),
                    disabled=not bool(draft_branches),
                )

        instruction = st.text_area("Instruction", placeholder="Create an issue for missing input validation in login API.")
        if st.button("Draft from instruction"):
            if not (engine.owner and engine.repo):
                st.error("Select a repository card first.")
            elif not instruction.strip():
                st.error("Instruction cannot be empty.")
            elif instruction_kind == "pr" and not draft_branches:
                st.error("No remote branches available. Refresh remote branches and try again.")
            elif instruction_kind == "pr" and pr_source_branch == pr_target_branch:
                st.error("PR source and target branches must be different.")
            else:
                if instruction_kind == "pr":
                    st.session_state.source_branch = pr_source_branch
                    st.session_state.target_branch = pr_target_branch

                queued = engine.draft_from_instruction(
                    instruction_kind,
                    instruction,
                    source_branch=pr_source_branch,
                    target_branch=pr_target_branch,
                )
                st.session_state.last_draft = queued
                st.rerun()

    last_draft = st.session_state.get("last_draft")
    if last_draft:
        render_draft_payload(last_draft, heading="Draft Ready for Approval")

        plan = last_draft.get("plan")
        if isinstance(plan, dict):
            render_plan(plan)

        reflection = last_draft.get("reflection")
        if isinstance(reflection, dict):
            render_reflection(reflection)

with tabs[2]:
    st.subheader("Task 3: Improve Existing Issue/PR")
    improve_kind = st.selectbox("Artifact type", ["issue", "pr"])
    number = st.number_input("Issue/PR number", min_value=1, step=1)
    if st.button("Improve"):
        if not (engine.owner and engine.repo):
            st.error("Select a repository card first.")
        else:
            improved = engine.improve_existing(improve_kind, int(number))
            st.session_state.last_improvement = improved
            st.rerun()

    improvement = st.session_state.get("last_improvement")
    if improvement:
        source = improvement.get("source") or {}
        with st.container(border=True):
            st.markdown("**Current Artifact**")
            st.write(f"Title: {source.get('title', 'n/a')}")
            st.write(f"State: {source.get('state', 'n/a')}")
            html_url = source.get("html_url")
            if html_url:
                st.markdown(f"Link: [{html_url}]({html_url})")

        with st.container(border=True):
            st.markdown("**Critique**")
            st.write(improvement.get("critique", "No critique generated."))

        with st.container(border=True):
            st.markdown("**Proposed Improved Version**")
            st.markdown(improvement.get("improved_markdown", "No improved version generated."))

        plan = improvement.get("plan")
        if isinstance(plan, dict):
            render_plan(plan)

        reflection = improvement.get("reflection")
        if isinstance(reflection, dict):
            render_reflection(reflection)

with tabs[3]:
    st.subheader("Approval Queue")
    queue = engine.list_pending_queue()
    if not queue:
        st.info("No pending drafts.")

    for item in queue:
        draft = item.get("draft") or {}
        with st.container(border=True):
            st.markdown(f"### Draft ID: {item.get('draft_id', 'n/a')}")
            st.write(f"Status: {item.get('status', 'n/a')}")
            st.write(f"Type: {draft.get('kind', 'n/a')}")
            st.write(f"Title: {draft.get('title', 'n/a')}")
            st.write(f"Risk level: {draft.get('risk_level', 'n/a')}")

            markdown_preview = draft.get("markdown")
            if markdown_preview:
                with st.expander("View Draft Markdown"):
                    st.markdown(markdown_preview)

            col1, col2 = st.columns(2)
            with col1:
                if st.button(f"Approve and Create {item['draft_id']}"):
                    st.session_state.dismissed_gatekeeper_dialog = ""
                    approval = engine.approve_draft(item["draft_id"], approved=True)
                    st.session_state.last_approval_result = approval
            with col2:
                if st.button(f"Reject {item['draft_id']}"):
                    rejection = engine.approve_draft(item["draft_id"], approved=False)
                    st.session_state.last_approval_result = rejection

    approval_result = st.session_state.get("last_approval_result")
    if approval_result:
        if approval_result.get("error"):
            st.error(approval_result["error"])
        message = str(approval_result.get("message") or "")
        if message:
            st.info(message)
            if GATEKEEPER_FAIL_MESSAGE in message:
                dismissed = str(st.session_state.get("dismissed_gatekeeper_dialog") or "")
                if dismissed != message:
                    show_gatekeeper_fail_dialog(message)

        record = approval_result.get("record") or {}
        if record:
            with st.container(border=True):
                st.markdown("**Approval Result**")
                st.write(f"Status: {record.get('status', 'n/a')}")
                st.write(f"Reason: {record.get('reason', 'n/a')}")
                st.write(f"Draft ID: {record.get('draft_id', 'n/a')}")
                if record.get("github_url"):
                    link = record["github_url"]
                    st.markdown(f"GitHub URL: [{link}]({link})")

        gh_response = approval_result.get("github_response") or {}
        if gh_response and not gh_response.get("error"):
            with st.container(border=True):
                st.markdown("**Created Artifact**")
                st.write(f"Number: {gh_response.get('number', 'n/a')}")
                st.write(f"Title: {gh_response.get('title', 'n/a')}")
                st.write(f"State: {gh_response.get('state', 'n/a')}")
                if gh_response.get("html_url"):
                    link = gh_response["html_url"]
                    st.markdown(f"Link: [{link}]({link})")
