"""The admin API's OpenAPI 3.1 document (A1), rendered deterministically.

The committed copy is controlplane/openapi/admin-api.json; CI regenerates it
and fails on any difference, and the console's TypeScript client is generated
from it. Rendering builds the routes only: no settings, database, or network.
"""

import json
from typing import Any

from purser_controlplane.app import build_app


def document() -> dict[str, Any]:
    return build_app().openapi()


def render() -> str:
    return json.dumps(document(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
