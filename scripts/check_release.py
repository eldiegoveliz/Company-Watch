"""Check Git's public file candidates without reading ignored private files.

Run from the project root: python3 -m scripts.check_release --list
"""
from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ROOT_FILES = {
    ".env.example", ".gitignore", "README.md", "LICENSE", "CONTRIBUTING.md", "AGENTS.md",
    "sources.example.json", "watched_tickers.example.txt",
    "sources.biotech.example.json", "watched_tickers.biotech.example.txt",
}
PRIVATE_FILES = {"sources.json", "watched_tickers.txt", "first-instructions.txt"}
CONTENT_CHECKS = {
    "possible API key": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,})"),
    "possible Telegram bot token": re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{25,}"),
    "private key": re.compile(r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----"),
    "personal absolute path": re.compile(r"/(?:root|home|Users)/[A-Za-z0-9_.-]+/"),
    "populated credential or chat ID": re.compile(
        r"^(?:OPENAI_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID)"
        r"[ \t]*=[ \t]*(?:[\"'][^\"'\r\n]+[\"']|[^\s#'\"])", re.MULTILINE,
    ),
}


def public_files(root: Path) -> list[str]:
    # A temporary Git directory lets this check run before the first git init.
    with tempfile.TemporaryDirectory(prefix="company-watch-release-") as tmp:
        if (root / ".git").exists():
            git = ["git", "-C", str(root)]
        else:
            subprocess.run(["git", "init", "--quiet", tmp], check=True, capture_output=True)
            git = ["git", f"--git-dir={tmp}/.git", f"--work-tree={root}"]
        output = subprocess.check_output([
            *git, "-c", "core.excludesFile=/dev/null", "ls-files",
            "--cached", "--others", "--exclude-standard", "-z",
        ])
    return sorted(set(output.decode("utf-8").strip("\0").split("\0")) - {""})


def allowed(name: str) -> bool:
    path = Path(name)
    if name in ROOT_FILES:
        return True
    if path.parts[0] in {"company_watch", "biotech_newswatch", "scripts", "tests"}:
        return path.suffix == ".py"
    if path.parts[0] == "docs":
        return path.suffix == ".md"
    if path.parent == Path("systemd"):
        return path.suffix == ".service"
    return path.parent == Path(".github/workflows") and path.suffix in {".yml", ".yaml"}


def check(root: Path, files: list[str], require_license: bool = False) -> list[str]:
    problems = []
    for name in files:
        path = root / name
        if path.name in PRIVATE_FILES or (path.name.startswith(".env") and name != ".env.example"):
            problems.append(f"{name}: private configuration must stay untracked")
            continue
        if not allowed(name):
            problems.append(f"{name}: unexpected public file; review the release allowlist")
            continue
        if path.is_symlink():
            problems.append(f"{name}: symlinks are not allowed in the release")
            continue
        if not path.is_file():
            problems.append(f"{name}: missing file; stage its deletion")
            continue
        if path.stat().st_size > 1_000_000:
            problems.append(f"{name}: file exceeds the 1 MB source-file limit")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"{name}: expected a UTF-8 text file")
            continue
        for label, pattern in CONTENT_CHECKS.items():
            if pattern.search(content):
                # Print the path and category, never a matched credential.
                problems.append(f"{name}: {label}")
    if require_license and "LICENSE" not in files:
        problems.append("LICENSE: choose and add a license before publishing")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list files Git would include")
    parser.add_argument("--require-license", action="store_true", help="fail if LICENSE is absent")
    args = parser.parse_args()
    files = public_files(ROOT)
    if args.list:
        print("\n".join(files))
    problems = check(ROOT, files, args.require_license)
    for problem in problems:
        print(f"FAIL {problem}")
    if not problems:
        print(f"PASS {len(files)} public file candidates checked")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
