# Personalized GitHub Repository Agent

Streamlit app implementing a multi-agent GitHub workflow with explicit role separation:
- `Reviewer` (LLM): analyzes code evidence
- `Planner` (LLM): decides action (`create_issue`, `create_pr`, `no_action`)
- `Writer` (LLM): drafts issue/PR content
- `Gatekeeper` (LLM + policy checks): verifies safety and enforces human approval before creation

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Configure

Set environment variables:

```bash
set GITHUB_TOKEN=your_token
set OLLAMA_BASE_URL=http://localhost:11434
set OLLAMA_MODEL=devstral-small-2:24b-cloud
set OLLAMA_TIMEOUT_SECONDS=60
```

Start Ollama locally and ensure the selected model is pulled.

## Features

- Token-based repository discovery and card selection.
- Task 1: Review current branch or commit range.
- Task 2: Draft Issue/PR from review or explicit instruction.
- Task 3: Improve existing Issue/PR with critique-first rewrite.
- Human approval gate before any GitHub create action.
- Reflection artifacts persisted under `artifacts/`.
