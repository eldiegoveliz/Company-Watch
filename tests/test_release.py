from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.check_release import ROOT, check, public_files


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".gitignore").write_text((ROOT / ".gitignore").read_text())
        (self.root / "README.md").write_text("# Example\n")

    def test_private_files_stay_out_of_public_candidates(self):
        for name in [
            ".env", ".env.backup", "sources.json", "watched_tickers.txt",
            "first-instructions.txt", "live.sqlite", "live.sqlite-wal", "live.sqlite-shm",
        ]:
            (self.root / name).write_text("private data")
        (self.root / ".env.example").write_text("TELEGRAM_BOT_TOKEN=\nOPENAI_API_KEY=\n")
        files = public_files(self.root)
        self.assertEqual(files, [".env.example", ".gitignore", "README.md"])
        self.assertEqual(check(self.root, files), [])

    def test_forced_tracked_secret_is_rejected_without_reading_it(self):
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        (self.root / ".env").mkdir()  # Reading it as a file would raise an error.
        # Use an index entry so even a missing or unreadable tracked secret is rejected.
        subprocess.run([
            "git", "-C", str(self.root), "update-index", "--add", "--cacheinfo",
            "100644,e69de29bb2d1d6434b8b29ae775ad8c2e48c5391,.env",
        ], check=True)
        problems = check(self.root, public_files(self.root))
        self.assertIn(".env: private configuration must stay untracked", problems)

    def test_secret_findings_do_not_print_values(self):
        token = "123456789:" + "A" * 35
        (self.root / "README.md").write_text(token)
        problems = check(self.root, public_files(self.root))
        self.assertEqual(problems, ["README.md: possible Telegram bot token"])
        self.assertNotIn(token, "\n".join(problems))

    def test_quoted_credentials_are_rejected(self):
        (self.root / ".env.example").write_text('OPENAI_API_KEY="example-secret"\n')
        self.assertIn(
            ".env.example: populated credential or chat ID",
            check(self.root, public_files(self.root)),
        )

    def test_unexpected_files_and_missing_license_block_release(self):
        (self.root / "private-notes.txt").write_text("local notes")
        problems = check(self.root, public_files(self.root), require_license=True)
        self.assertEqual(len(problems), 2)
        self.assertTrue(any(p.startswith("private-notes.txt:") for p in problems))
        self.assertTrue(any(p.startswith("LICENSE:") for p in problems))

    def test_github_actions_workflows_are_not_public_release_files(self):
        workflow = self.root / ".github/workflows/checks.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("name: Checks\non: push\n")
        problems = check(self.root, public_files(self.root))
        self.assertTrue(any(p.startswith(".github/workflows/checks.yml:") for p in problems))


if __name__ == "__main__":
    unittest.main()
