# Work on Company Watch

This project can be adapted by coding agents. Preserve the user's preferences
and explain changes in plain language.

## Preserve existing installations

Use `company_watch` for new imports and commands. `biotech_newswatch` is a
compatibility package for existing commands and imports. Preserve this entry
point and the legacy database selection in `company_watch/config.py`.
Do not move a live database or change a user's profile during a naming cleanup.

Monitoring profiles live in `company_watch/profiles.py`. Fresh installations use
`general`; `biotech` adds sector-specific rules and model context. Keep profiles
consistent across classification, HTML source parsing, and model adapters.
Document any additional profile in `docs/profiles.md` and test its actual path
from source parsing through saved analysis and alert formatting.

## Set up analysis with the user

- Read `docs/analysis.md` and inspect the existing configuration without
  displaying credentials. Honor an established provider and model preference.
- The agent or editor working on the repository is separate from the bot's
  runtime analyzer. Do not infer the analyzer from the agent's brand, login,
  available tools, or installed editor.
- If the preference is unclear, explain the implemented options and ask which
  provider and model the user wants, with a local CLI or API key. Continue work
  that does not depend on that choice while waiting.
- Explain whether the choice needs an executable, separate authentication, or
  an API key. Explain which data leaves the machine and that API usage may cost
  money. Do not silently switch to a paid provider.
- Keep secrets in the ignored `.env` or process environment. Do not request
  keys in chat, print them, or add them to examples or commits.
- Run `python3 -m scripts.check_analysis` after setup. Report what it proves:
  local prerequisites only. Do not describe it as a successful real model call.

## Adapt or add an analyzer

Built-in adapters are in `company_watch/analyzer.py`. Settings and environment
loading are in `company_watch/config.py`. Add a provider here when the user's
preferred option needs one. Verify its current interface from official docs.

- Keep provider selection explicit. Extend `analysis_configuration` and
  `analysis_setup_issues` with any new adapter.
- Preserve the analysis result fields used by `format_alert`: `alert`, `company`,
  `ticker`, `event`, `event_type`, `why_it_matters`, `thesis_impact`, `urgency`,
  `confidence`, `uncertainty`, and `source`. Return a boolean for `alert` and a list
  of strings for `why_it_matters`.
- Preserve analysis job records and the local fallback on provider failure.
  Keep database writes outside provider exception handlers that mask failures.
- Add offline tests with mocked external calls and temporary databases. Update
  `.env.example` and `docs/analysis.md` for new settings.
- Keep README edits focused on the requested setup details. Preserve the
  maintainer's human-written introduction and explanations.
- Report the selected provider and model, edits, checks, and any setup still
  needed. Use existing authorization before making real provider calls or
  restarting a service. Never claim an integration works based only on a mock.

Run `python3 -m unittest discover -s tests -v` and
`python3 -m scripts.check_release --require-license` before finishing code changes.
