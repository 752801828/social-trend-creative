# Repository maintenance rules

- Keep this project independent from Flow2API and Gemini2API. Integrate only through documented HTTP APIs.
- Update `docs/PROJECT_OVERVIEW_AND_CHANGELOG.md` in every code, configuration, UI, API, data, or deployment commit.
- Never commit API keys, cookies, webhook URLs, signing secrets, or generated media.
- Keep scheduled execution disabled by default until manual discovery and generation have been verified on the deployment host.

