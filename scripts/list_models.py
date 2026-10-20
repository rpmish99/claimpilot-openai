"""List GPT models available to your API key (to choose EXTRACTION_MODEL / RESOLUTION_MODEL)."""
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
ids = sorted(m.id for m in OpenAI().models.list() if m.id.startswith(("gpt-", "o")))
print("\n".join(ids))
