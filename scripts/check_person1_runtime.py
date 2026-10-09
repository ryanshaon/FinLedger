"""Check the installed Person 1 wheel; run with python -I outside the source tree."""

import json
import os
import sys
from importlib import import_module
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path


MODULES = (
    "contracts.schemas",
    "contracts.export_schemas",
    "extract.vision_fallback",
    "mapper.map_worker",
    "memory.memory_indexer",
    "rag.retriever",
    "safety.risk_worker",
    "summary.summary_worker",
    "usage.model_router",
)
PROMPTS = (
    ("extract", "extract_digital.txt"),
    ("mapper", "map_prompt.txt"),
    ("safety", "risk_prompt.txt"),
)
SCHEMAS = (
    "canonical_invoice", "correction_event", "map_result", "map_trace", "risk_score", "voucher_draft",
)


def main():
    package_version = version("finledger-ai")
    for name in MODULES:
        module = import_module(name)
        if not Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
            raise RuntimeError(f"{name} was imported from outside the installed environment")
    for package, filename in PROMPTS:
        if not files(package).joinpath("prompts", filename).read_text(encoding="utf-8").strip():
            raise RuntimeError(f"Empty packaged prompt: {package}/{filename}")
    for name in SCHEMAS:
        schema = json.loads(files("contracts").joinpath("json_schemas", f"{name}.json").read_text(encoding="utf-8"))
        if schema.get("type") != "object":
            raise RuntimeError(f"Invalid packaged schema: {name}")

    from usage.model_router import ModelRouter, Settings

    for environment in ("staging", "production"):
        os.environ["FINLEDGER_ENV"] = environment
        try:
            ModelRouter(Settings(provider="mock"))
        except RuntimeError as error:
            if str(error) != "mock LLM provider cannot run in a hosted environment":
                raise
        else:
            raise RuntimeError(f"Mock provider was accepted in {environment}")
    print(f"finledger-ai {package_version}: {len(MODULES)} imports, {len(PROMPTS)} prompts, "
          f"{len(SCHEMAS)} schemas and both hosted mock guards passed")


if __name__ == "__main__":
    main()
