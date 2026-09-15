from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path.cwd()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    main_path = ROOT / "main.py"
    text = main_path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "    is_safe_remote_url, trim_cache_directory,\n",
        "    is_safe_remote_url, response_peer_is_global, trim_cache_directory,\n",
        "main peer helper import",
    )
    ast.parse(text, filename=str(main_path))
    main_path.write_text(text, encoding="utf-8", newline="\n")

    test_path = ROOT / "audit" / "regression_v42_full.py"
    test = test_path.read_text(encoding="utf-8")
    test = test.replace('unknown = model_tags.model_tag_manager.resolve_model("__v42_nonexistent_fixture__")\n# Avoid live network dependence: source contract is also asserted statically below.\n', '# Source contract is asserted statically; no live provider request is made in CI.\n')
    ast.parse(test, filename=str(test_path))
    test_path.write_text(test, encoding="utf-8", newline="\n")

    print("V4.2 follow-up corrections applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
