"""Check analyzer configuration without calling a model or opening a database.

Run from the project root: python3 -m scripts.check_analysis
"""
from company_watch.analyzer import analysis_configuration, analysis_setup_issues
from company_watch.config import get_settings


def main() -> int:
    settings = get_settings()
    provider, model, _ = analysis_configuration(settings)
    print(f"Analysis provider: {provider}")
    print(f"Analysis model: {model}")
    print(f"Watch profile: {settings.watch_profile}")
    issues = analysis_setup_issues(settings)
    for issue in issues:
        print(f"FAIL {issue}")
    if not issues:
        print("PASS Local analyzer prerequisites are present.")
    print("No model request was sent. Authentication, model access, and output are not verified.")
    print("To change providers, edit .env using docs/analysis.md. API keys are not displayed.")
    return int(bool(issues))


if __name__ == "__main__":
    raise SystemExit(main())
