"""Resolve configured local storage roots consistently in API and offline scripts."""

from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]


def configured_storage_path(value: str) -> Path:
    """Relative storage paths are anchored to backend/, regardless of process cwd."""
    path = Path(value).expanduser()
    return (path if path.is_absolute() else BACKEND_DIR / path).resolve()
