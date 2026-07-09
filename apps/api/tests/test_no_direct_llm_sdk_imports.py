"""Enforce that provider SDKs stay behind the gateway modules.

The grounded-RAG contract depends on every LLM chat call going through
``app/services/llm.py``. This test fails if any other module imports a provider
chat SDK directly, catching accidental bypasses in review/CI.
"""

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# SDK import token -> modules (relative to app/) allowed to import it.
ALLOWED = {
    "litellm": {"services/llm.py"},
    "anthropic": {"services/llm.py"},
    # Embeddings have their own gateway module (plan 1.4); it owns the OpenAI SDK.
    "openai": {"services/llm.py", "services/embeddings.py"},
}

IMPORT_RE = {
    sdk: re.compile(rf"^\s*(?:import\s+{sdk}\b|from\s+{sdk}\b)", re.MULTILINE) for sdk in ALLOWED
}


def test_provider_sdks_only_imported_by_gateway() -> None:
    violations: list[str] = []
    for path in APP_DIR.rglob("*.py"):
        rel = path.relative_to(APP_DIR).as_posix()
        source = path.read_text()
        for sdk, pattern in IMPORT_RE.items():
            if pattern.search(source) and rel not in ALLOWED[sdk]:
                violations.append(f"{rel} imports {sdk}")
    assert not violations, "provider SDK imported outside gateway: " + ", ".join(violations)
