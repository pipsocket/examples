"""Write tests/openapi.json from the API's published OpenAPI document.

The mock checks requests and answers against it. Only the structure is kept (paths and schemas):
descriptions, examples and extensions are left out, as the checks don't use them.

    uv run python tests/snapshot_openapi.py
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import httpx

URL = "https://docs.pipsocket.com/openapi.json"
PROSE = {"description", "summary", "examples", "example"}


def structure(node: Any, in_properties: bool = False) -> Any:
    """The document without prose or extensions. Under `properties` every key is a field name."""
    if isinstance(node, dict):
        return {
            key: structure(value, key == "properties")
            for key, value in node.items()
            if in_properties or (key not in PROSE and not key.startswith("x-"))
        }
    if isinstance(node, list):
        return [structure(value) for value in node]
    return node


def main() -> None:
    answer = httpx.get(URL, headers={"User-Agent": "pipsocket-examples"}, follow_redirects=True)
    answer.raise_for_status()
    path = pathlib.Path(__file__).parent / "openapi.json"
    path.write_text(json.dumps(structure(answer.json()), indent=2) + "\n")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
