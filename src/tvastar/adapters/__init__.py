"""tvastar.adapters — Loop Quality for any agent framework.

Tvastar becomes the quality layer you add on top of whatever agent
infrastructure you already run. Each adapter converts that framework's
message format into Tvastar's types so the full silent-failure detector
suite can run — not just text-level checks.

Available adapters::

    from tvastar.adapters import openai, langgraph, agentcore

    # OpenAI function-calling loops
    from tvastar.adapters.openai import OpenAILoopWrapper, score_openai_messages

    # LangGraph graphs
    from tvastar.adapters.langgraph import LangGraphWrapper

    # AWS AgentCore (Bedrock Agents)
    from tvastar.adapters.agentcore import AgentCoreWrapper, score_agentcore_response
"""
