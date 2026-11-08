"""Fail if a committed file looks like it contains a secret. Used by CI and safe to run locally:  python scripts/secret_scan.py

Scans the files git tracks (so .env, which is git-ignored, is never read). Placeholders such as 'your-gemini-key' pass.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = {
    "Google API key": re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    "Google API key (new format)": re.compile(r"\bAQ\.[A-Za-z0-9_\-]{30,}"),
    "Google OAuth client secret": re.compile(r"GOCSPX-[A-Za-z0-9_\-]{20,}"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    "Vercel token": re.compile(r"\b(?:vercel_[A-Za-z0-9]{20,}|vcp_[A-Za-z0-9]{20,})"),
    "Private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"),
    "Database URL with a password": re.compile(r"postgres(?:ql)?://[^\s:@/'\"]+:(?!your|password|PASSWORD|\*|<|\$|%)[^\s@'\"]{4,}@"),
    "PGPASSWORD set to a value": re.compile(r"PGPASSWORD\s*=\s*(?!your|<|\$|\"\"|''|\s*$)[^\s#]{6,}"),
}
SKIP_SUFFIX = {".db", ".zip", ".png", ".jpg", ".jpeg", ".webp", ".pdf", ".ico", ".lock"}
SKIP_FILES = {"scripts/secret_scan.py"}           # this file names the patterns
MAX_BYTES = 2_000_000


SKIP_DIRS = {".git", ".venv", "_srcdata", "node_modules", "__pycache__", ".pytest_cache", ".vercel"}


def tracked_files() -> list[Path]:
    """The files git tracks. Without git (for example in an unzipped copy), every file except ignored folders and .env files."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8", "replace")
        files = [ROOT / p for p in out.split("\0") if p]
        if files:
            return files
    except (OSError, subprocess.CalledProcessError):
        pass
    return [p for p in ROOT.rglob("*")
            if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS) and not p.name.startswith(".env") or p.name == ".env.example"]


def scan(paths: list[Path], root: Path = ROOT) -> list[str]:
    problems: list[str] = []
    for p in paths:
        rel = p.relative_to(root).as_posix()
        if rel in SKIP_FILES or p.suffix.lower() in SKIP_SUFFIX or not p.is_file() or p.stat().st_size > MAX_BYTES:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for name, rx in PATTERNS.items():
            m = rx.search(text)
            if m:
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{rel}:{line}: looks like a {name} ({m.group(0)[:6]}...)")
    return problems


def main() -> int:
    files = tracked_files()
    problems = scan(files)
    if problems:
        print("Possible secrets found in tracked files:")
        for p in problems:
            print("  -", p)
        print("\nRemove the value, put it in .env or the host's settings, and rotate it if it was ever pushed.")
        return 1
    print(f"No secrets found in {len(files)} tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
