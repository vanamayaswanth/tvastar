"""Pipeline Generator Agent — creates CI/CD pipelines from natural language.

Showcases:
- Structured output (result= produces validated Pydantic models)
- DAG task execution (TaskGraph — parallel analysis + sequential generation)
- GovernancePolicy phase transitions (analyze → generate → validate → deploy)
- Tool masking (agent only sees relevant tools per phase)
- Observability (Tracer + JSONLExporter for debugging)
- MCP integration pattern (extensible to any tool server)

Usage:
    export ANTHROPIC_API_KEY=sk-...
    python examples/pipeline_generator.py
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel

from tvastar import (
    Harness,
    TaskGraph,
    create_agent,
    tool,
)
from tvastar.masking import GovernancePolicy
from tvastar.model import MockModel  # swap for AnthropicModel in production
from tvastar.observability import ConsoleExporter, Tracer


# ---------------------------------------------------------------------------
# Structured output schemas
# ---------------------------------------------------------------------------


class PipelineStage(BaseModel):
    """A single stage in a CI/CD pipeline."""
    name: str
    image: str
    commands: list[str]
    depends_on: list[str] = []
    environment: dict[str, str] = {}


class Pipeline(BaseModel):
    """A complete CI/CD pipeline definition."""
    name: str
    trigger: str
    stages: list[PipelineStage]
    notifications: list[str] = []


class ProjectAnalysis(BaseModel):
    """Analysis of a project's tech stack and requirements."""
    language: str
    framework: str
    test_command: str
    build_command: str
    deploy_target: str
    has_docker: bool
    has_kubernetes: bool


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
async def analyze_project(path: str = ".") -> str:
    """Analyze a project's technology stack and structure."""
    return json.dumps({
        "language": "python",
        "framework": "fastapi",
        "test_command": "pytest tests/ -q",
        "build_command": "docker build -t app .",
        "deploy_target": "kubernetes",
        "has_docker": True,
        "has_kubernetes": True,
        "dependencies": ["fastapi", "uvicorn", "pydantic", "sqlalchemy"],
    })


@tool
async def validate_pipeline_yaml(yaml_content: str) -> str:
    """Validate that a pipeline YAML is syntactically correct."""
    # In production: actual YAML schema validation
    if "stages:" in yaml_content or "steps:" in yaml_content:
        return "✅ Pipeline YAML is valid. No schema errors found."
    return "❌ Invalid: missing 'stages' or 'steps' key."


@tool
async def generate_dockerfile(base_image: str, commands: list) -> str:
    """Generate a Dockerfile for the project."""
    return f"FROM {base_image}\nWORKDIR /app\nCOPY . .\nRUN pip install -e .\nCMD [\"uvicorn\", \"app:main\"]"


@tool
async def write_file(path: str, content: str) -> str:
    """Write content to a file."""
    # In production: actual file write
    return f"Wrote {len(content)} bytes to {path}"


@tool
async def commit_and_push(message: str) -> str:
    """Commit changes and push to the remote branch."""
    return f"Committed: '{message}' → pushed to origin/add-ci-pipeline"


# ---------------------------------------------------------------------------
# Governance — phased pipeline generation
# ---------------------------------------------------------------------------

governance = GovernancePolicy(
    phases={
        "analyze": {"analyze_project"},
        "generate": {"analyze_project", "generate_dockerfile", "write_file"},
        "validate": {"validate_pipeline_yaml"},
        "deploy": {"write_file", "commit_and_push"},
    },
    current_phase="analyze",
)

# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

agent = create_agent(
    "pipeline-generator",
    model=MockModel([
        # Task 1: Analyze
        json.dumps({
            "language": "python",
            "framework": "fastapi",
            "test_command": "pytest tests/ -q",
            "build_command": "docker build -t app .",
            "deploy_target": "kubernetes",
            "has_docker": True,
            "has_kubernetes": True,
        }),
        # Task 2: Generate pipeline
        json.dumps({
            "name": "ci-cd-pipeline",
            "trigger": "push to main",
            "stages": [
                {"name": "lint", "image": "python:3.11", "commands": ["ruff check ."], "depends_on": []},
                {"name": "test", "image": "python:3.11", "commands": ["pytest tests/ -q"], "depends_on": ["lint"]},
                {"name": "build", "image": "docker:24", "commands": ["docker build -t app ."], "depends_on": ["test"]},
                {"name": "deploy", "image": "bitnami/kubectl", "commands": ["kubectl apply -f k8s/"], "depends_on": ["build"], "environment": {"KUBECONFIG": "/secrets/kubeconfig"}},
            ],
            "notifications": ["slack:#deploys"],
        }),
        # Task 3: Validate
        "Pipeline validated successfully. All stages have valid images and commands.",
    ]),
    instructions="""You are a CI/CD pipeline generator. Given a project description:
1. ANALYZE the project structure and tech stack
2. GENERATE a complete pipeline with: lint, test, build, deploy stages
3. VALIDATE the generated pipeline for correctness
4. Output the result as structured data

Rules:
- Always include a lint and test stage before build.
- Use appropriate Docker images for each stage.
- Set up proper stage dependencies (test depends on lint, build depends on test).
- Include environment variables for secrets (never hardcode them).
""",
    tools=[analyze_project, validate_pipeline_yaml, generate_dockerfile,
           write_file, commit_and_push],
    governance=governance,
    max_steps=15,
)

# ---------------------------------------------------------------------------
# DAG execution — parallel analysis + sequential generation
# ---------------------------------------------------------------------------


async def main():
    print("🏗️  Pipeline Generator Agent")
    print("=" * 50)

    # Create tracer for observability
    tracer = Tracer([ConsoleExporter()])
    harness = Harness(agent, tracer=tracer)

    # Use TaskGraph for parallel + sequential execution
    graph = TaskGraph(harness)
    graph.task("analyze", "Analyze the project at '.' and determine the tech stack", result=ProjectAnalysis)
    graph.task("generate", "Generate a complete CI/CD pipeline for this Python FastAPI project with Docker and Kubernetes deployment", depends_on=["analyze"], result=Pipeline)
    graph.task("validate", "Validate the generated pipeline is correct and complete", depends_on=["generate"])

    print("\n🔄 Executing pipeline generation DAG...\n")
    results = await graph.run()

    # Show structured output
    print(f"\n📊 Results:")
    print(f"   Tasks completed: {len(results)}")
    print(f"   All OK: {results.ok}")

    if results["analyze"].data:
        analysis = results["analyze"].data
        print(f"\n🔍 Project Analysis:")
        print(f"   Language: {analysis.language}")
        print(f"   Framework: {analysis.framework}")
        print(f"   Deploy target: {analysis.deploy_target}")

    if results["generate"].data:
        pipeline = results["generate"].data
        print(f"\n📋 Generated Pipeline: '{pipeline.name}'")
        print(f"   Trigger: {pipeline.trigger}")
        print(f"   Stages:")
        for stage in pipeline.stages:
            deps = f" (after: {', '.join(stage.depends_on)})" if stage.depends_on else ""
            print(f"     → {stage.name}: {stage.image}{deps}")
            for cmd in stage.commands:
                print(f"       $ {cmd}")

    print(f"\n✅ Pipeline generation complete!")


if __name__ == "__main__":
    asyncio.run(main())
