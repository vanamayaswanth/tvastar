"""Quick smoke test with Groq to confirm real model works with Tvastar."""
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from tvastar import Harness, create_agent, default_toolset
from tvastar.model import OpenAIModel

async def main():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("ERROR: GROQ_API_KEY not set in .env")
        return

    model = OpenAIModel(
        model="llama-3.1-8b-instant",
        base_url="https://api.groq.com/openai/v1",
        api_key=api_key,
    )

    agent = create_agent(
        "groq-test",
        model=model,
        instructions="You are a helpful assistant. Be concise.",
        tools=default_toolset(),
        max_steps=3,
    )

    print("Testing Tvastar + Groq (llama-3.1-8b-instant)...")
    result = await Harness(agent).run("What is 2 + 2? Answer in one word.")

    print(f"Response: {result.text}")
    print(f"Quality:  {result.quality.grade} (score: {result.quality.score})")
    print(f"Steps:    {result.steps}")
    print(f"Cost:     ${result.cost.usd:.6f}")
    print(f"OK:       {result.ok}")

if __name__ == "__main__":
    asyncio.run(main())
