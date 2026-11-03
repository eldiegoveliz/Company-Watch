# Choose and check an analyzer

Choose the provider and model the bot uses to analyze news. Your coding agent
or editor can help with setup, but its own login does not automatically give
the bot access to that provider.

## Use the local Codex CLI

In `.env`, set:

```env
ANALYSIS_PROVIDER=codex
CODEX_COMMAND=codex
```

Set `CODEX_MODEL` to a model available to your account. Install and authenticate
the Codex CLI for the account that runs the bot. For a service, set
`CODEX_COMMAND` to the absolute executable path if it is absent from the service's
search path. This adapter uses CLI authentication and does not need the bot's
`OPENAI_API_KEY` setting.

## Use an API key

For the built-in OpenAI adapter, set `ANALYSIS_PROVIDER=openai_api` and choose
`OPENAI_MODEL`. Add your key to `OPENAI_API_KEY` in your local `.env`.

For the built-in Gemini adapter, set `ANALYSIS_PROVIDER=gemini` and choose
`GEMINI_MODEL`. Add your key to `GEMINI_API_KEY` in your local `.env`.
`GOOGLE_API_KEY` is also accepted if `GEMINI_API_KEY` is absent.

Keep keys out of chat messages, source files, and commits. API analysis sends
the news item's title, summary, source link, and up to 12,000 characters of
collected source text to the selected service. Provider usage may incur charges.

Environment variables already set in the process take precedence over `.env`.
If a setting change has no effect, check the service's environment too.

## Check the configuration

From the project root, run:

```bash
python3 -m scripts.check_analysis
```

The command shows the selected provider and model, then checks for the executable
or API key it needs. It does not display keys, call a model, send Telegram
messages, or open the database. A nonzero exit status means local setup needs
attention. A pass does not verify authentication, account access to the model,
or the quality of its output.

Restart a running bot after changing its settings. If analysis fails, the bot
records the error and generates a local rule-based fallback. It does not switch
to another model provider automatically.

## Use another provider with an agent's help

Tell the agent which provider and model you prefer, and whether you want a local
command or an API. If you have not chosen, have it explain the available options
and ask before selecting a provider. Read [AGENTS.md](../AGENTS.md) for the setup
and adapter instructions.

Additional providers need code changes. Changing `ANALYSIS_PROVIDER` to an
unsupported name does not install an integration. Have the agent verify the
provider's current interface, add the adapter, and update the setup check,
examples, and offline tests. Have it report which changes it made and which
checks were local versus real provider calls.
