#!/usr/bin/env python3
"""Parse AmneziaWG clientsTable and emit public-key<TAB>client-name rows."""
from __future__ import annotations

import json
import sys
from typing import Any


def _clean_name(value: Any) -> str:
    if value is None:
        return ""
    # History/online state are semicolon-delimited; names must also be one line.
    return " ".join(str(value).replace(";", ",").split())


def parse_clients_table(payload: str) -> dict[str, str]:
    data = json.loads(payload)
    if not isinstance(data, list):
        raise ValueError("clientsTable root must be a JSON array")

    result: dict[str, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        client_id = str(item.get("clientId") or "").strip()
        user_data = item.get("userData")
        if not client_id or not isinstance(user_data, dict):
            continue
        client_name = _clean_name(user_data.get("clientName"))
        if client_name and client_id not in result:
            result[client_id] = client_name
    return result


def main() -> int:
    try:
        names = parse_clients_table(sys.stdin.read())
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"clientsTable parse error: {exc}", file=sys.stderr)
        return 2

    for client_id, client_name in names.items():
        print(f"{client_id}\t{client_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
