# Prepare a public release

Run these commands from the project root. Keep your live `.env`, database,
`sources.json`, `watched_tickers.txt`, and `first-instructions.txt` local.
Publish the `.example` configurations instead.

1. Keep the project's GPLv3 license text in `LICENSE` and its `GPL-3.0-only`
   declaration in the README with the release.
2. Run the offline tests:

   ```bash
   python3 -m unittest discover -s tests -v
   ```

3. Inspect the files Git would include:

   ```bash
   python3 -m scripts.check_release --list --require-license
   ```

   This command works before `git init` and does not read ignored private files.
   Update its allowlist when you intentionally add a new public file type.

4. If this directory has no Git repository, initialize one:

   ```bash
   git init -b main
   ```

5. Stage the public files, then inspect the staged content:

   ```bash
   git add .
   git diff --cached --stat
   git diff --cached
   python3 -m scripts.check_release --require-license
   ```

6. Run a dedicated secret scanner on the staged content. Review any findings
   before committing. The project check covers only a few credential formats.
7. Commit the reviewed files:

   ```bash
   git commit -m "Prepare Company Watch for public release"
   ```

8. Create the repository in your GitHub account, add its remote URL, and push
   `main`. Confirm the repository visibility before the push.

For a source archive, use `git archive` from a reviewed commit. Archiving the
whole working directory includes ignored private files.

The release check examines current files. It does not scan past commits. If a
credential has entered Git history, rotate it and remove it from that history
before making the repository public.
