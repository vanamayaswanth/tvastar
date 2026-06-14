---
name: Bug report
about: Something is broken or behaving unexpectedly
title: '[Bug] '
labels: bug
assignees: ''
---

## What happened?

<!-- A clear description of the bug. -->

## Reproduction

```python
# Minimal reproduction using MockModel + VirtualSandbox (no API key needed)
import asyncio
from tvastar import create_agent, Harness
from tvastar.model import MockModel

agent = create_agent("test", model=MockModel(script=["..."]))
result = asyncio.run(Harness(agent).run("..."))
print(result)
```

## Expected behaviour

<!-- What did you expect to happen? -->

## Actual behaviour

<!-- What actually happened? Paste the full traceback. -->

## Environment

- Tvastar version: <!-- `python -c "import tvastar; print(tvastar.__version__)"` -->
- Python version: <!-- `python --version` -->
- OS:
- Model provider (if relevant):
