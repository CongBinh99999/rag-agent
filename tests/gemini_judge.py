"""Gemini as deepeval judge. deepeval defaults to OpenAI/Anthropic; we only have a
Gemini key, so wrap ChatGoogleGenerativeAI as a DeepEvalBaseLLM.

Supports the `schema` kwarg deepeval passes for structured metrics (Faithfulness etc.)
via LangChain's with_structured_output -> avoids 'invalid JSON' failures.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from deepeval.models.base_model import DeepEvalBaseLLM  # noqa: E402

from src import config  # noqa: E402


class GeminiJudge(DeepEvalBaseLLM):
    def __init__(self):
        # temperature=0: judge must be deterministic. config.llm() is shared (and
        # temp-default) for the agent; a separate instance keeps judging stable
        # without changing how the agent answers.
        from langchain_google_genai import ChatGoogleGenerativeAI
        self.model = ChatGoogleGenerativeAI(
            model=config.LLM_MODEL, google_api_key=config.GOOGLE_API_KEY, temperature=0
        )

    def load_model(self):
        return self.model

    def generate(self, prompt: str, schema=None):
        if schema is not None:
            return self.model.with_structured_output(schema).invoke(prompt)
        res = self.model.invoke(prompt).content
        return res if isinstance(res, str) else "".join(
            b.get("text", "") for b in res if isinstance(b, dict)
        )

    async def a_generate(self, prompt: str, schema=None):
        return self.generate(prompt, schema)

    def get_model_name(self):
        return config.LLM_MODEL
