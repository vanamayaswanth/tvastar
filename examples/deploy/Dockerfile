# Container image for deploying a Tvastar agent as an HTTP/WebSocket service.
# Works on any container host: Render, Fly.io, Railway, Cloud Run, ECS, k8s.
#
#   docker build -f examples/deploy/Dockerfile -t tvastar .
#   docker run -p 8000:8000 -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY tvastar

FROM python:3.12-slim

WORKDIR /app

# Install uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml README.md ./
COPY tvastar ./tvastar
COPY examples ./examples

RUN uv pip install --system -e ".[serve,anthropic]"

EXPOSE 8000

# Serve the coding agent over HTTP + WebSocket on all interfaces.
CMD ["tvastar", "serve", "examples/coding_agent.py:agent", "--host", "0.0.0.0", "--port", "8000"]
