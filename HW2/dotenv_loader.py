"""
Load OPENAI_API_KEY (and other vars) from `.env` files before the rest of HW2 reads os.environ.

Canonical location: **repository root** — the parent directory of `HW2/` (e.g. `YourRepo/.env`).
Optional override: `HW2/.env` is loaded second with override=True for local development.
"""

from __future__ import annotations

from pathlib import Path


def load_hw2_dotenv() -> tuple[Path | None, Path | None]:
    """
    Load dotenv files. Returns (repo_root_env_path_or_none, hw2_env_path_or_none) if files exist.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return None, None

    hw2_dir = Path(__file__).resolve().parent
    repo_root = hw2_dir.parent

    root_env = repo_root / ".env"
    pkg_env = hw2_dir / ".env"

    loaded_root = root_env if root_env.is_file() else None
    loaded_pkg = pkg_env if pkg_env.is_file() else None

    if loaded_root is not None:
        load_dotenv(loaded_root, override=False)
    if loaded_pkg is not None:
        load_dotenv(pkg_env, override=True)

    return loaded_root, loaded_pkg
