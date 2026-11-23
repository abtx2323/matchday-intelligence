# Security

Matchday Intelligence is a demo. Every club, player, venue and match event is synthetic, and the app stores no user accounts or personal data.

What it does to stay safe:

- **No API keys.** Model access uses Microsoft Entra ID through `DefaultAzureCredential`: a managed identity in Azure Container Apps and `az login` locally. `.env` files are ignored by git; copy `.env.example` for local settings.
- **Spend and abuse limits.** Requests are rate limited per client, live streams are capped, and model calls have per-client and app-wide budgets that fall back to templates when they run out. These limits are in memory and per replica, so set a spending limit on the Azure subscription as a backstop.
- **Container.** The image runs as a non-root user, and Container Apps ingress is HTTPS only.
- **Dependencies** are pinned in `requirements*.txt`, and Dependabot proposes updates for Python packages and the Docker base image.

## Reporting a vulnerability

Please report security problems privately through GitHub's "Report a vulnerability" button on this repository's Security tab, not in a public issue. Include the steps to reproduce and the endpoint or file involved.
