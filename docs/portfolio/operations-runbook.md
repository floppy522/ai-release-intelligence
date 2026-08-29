# Operations Runbook

## Scope

This runbook is the safe local operating procedure for developers and
evaluators. It covers the production-style Compose stack in
[`compose.yaml`](../../compose.yaml) and the credential-free deterministic
stack in [`compose.test.yaml`](../../compose.test.yaml). The normal paths below
preserve PostgreSQL data. They do not constitute a production backup,
restore, retention, or incident-response procedure.

The Compose topology starts PostgreSQL, runs the one-shot `migrate` service,
then starts the API and web services. The `migrate` service runs Alembic
`upgrade head`; the API waits for both a healthy database and successful
migration, and the web service waits for a healthy API. See
[`compose.yaml`](../../compose.yaml), [`alembic/env.py`](../../apps/api/alembic/env.py),
and the [architecture deployment topology](architecture.md#deployment-topology).

## Prerequisites

- Docker Engine with Docker Compose v2 and permission to run `docker compose`.
- `curl` for the host health checks below.
- A free loopback port `8080` for the production-style web service, or set
  `ARI_WEB_PORT` in `.env`; the deterministic stack uses `4173`.
- For the production-style stack, a GitHub App installed only on repositories
  to be analyzed, plus its App and OAuth credentials. The deterministic stack
  uses fixture data and does not require those credentials.

Run commands from the repository root. Do not place `.env`, private keys,
OAuth secrets, database passwords, or provider keys in source control.

## GitHub App configuration

Create or use a GitHub App with repository-scoped, read-only access to
Metadata, Issues, Pull requests, Checks, Commit statuses, and Contents. Install
it only on the repositories this deployment is authorized to assess. The App ID
and RSA private key configure server-side installation-token minting; they are
not browser credentials. The required values are `ARI_GITHUB_APP_ID` and
`ARI_GITHUB_PRIVATE_KEY_PEM`.

Configure the App's GitHub OAuth client ID and secret as
`ARI_GITHUB_CLIENT_ID` and `ARI_GITHUB_CLIENT_SECRET`. Its authorization
callback URL must be exactly:

```text
http://localhost:8080/api/auth/github/callback
```

If `ARI_WEB_PORT` changes, update the origin portion of that callback URL to
the matching loopback port before testing login. Configure any ingress or
reverse proxy to omit query strings for `/api/auth/github/callback`; the
callback carries OAuth query values, and the application cannot sanitize logs
written upstream. The callback implementation and security behavior are in
[`auth.py`](../../apps/api/src/release_intelligence/api/routes/auth.py) and the
environment-file warning is in [`.env.example`](../../.env.example).

## Environment configuration

Start from the checked-in template and replace every placeholder with
deployment-specific values:

```bash
cp .env.example .env
```

`POSTGRES_PASSWORD` supplies the Compose database password. The API receives a
container-local PostgreSQL URL from Compose; `ARI_DATABASE_URL` in the template
is for local non-Compose use and must use `postgresql+asyncpg`. Generate a
separate valid Fernet key for `ARI_CREDENTIAL_ENCRYPTION_KEY`; it protects
stored OAuth credentials. `ARI_GITHUB_PRIVATE_KEY_PEM` must be an RSA private
key, and the GitHub App and OAuth values must all be non-empty.

Optional AI explanations are unsupported by the current checked-in Compose
topology: [`compose.yaml`](../../compose.yaml) does not pass any
`ARI_OPENAI_*` values to the API, and the API image does not receive the root
`.env` file. The production-style and deterministic Compose procedures in this
runbook therefore run without an AI provider.

The optional `ARI_OPENAI_*` settings in [`.env.example`](../../.env.example)
apply only to a separate non-Compose deployment that explicitly injects them
into the API. In that deployment, leave every `ARI_OPENAI_*` setting unset to
keep the provider disabled; if `ARI_OPENAI_API_KEY` is set, both current
configured input and output token prices are also required. An unavailable
optional explanation does not change the deterministic readiness assessment.
See [`config.py`](../../apps/api/src/release_intelligence/config.py).

## Start and verify

For the production-style local stack, validate substituted Compose configuration
before creating containers, then start and verify the loopback web health
endpoint:

```bash
cp .env.example .env
docker compose config --quiet
docker compose up --build -d --wait
curl --fail --silent --show-error http://127.0.0.1:8080/healthz
docker compose ps
docker compose down
```

Replace the `.env` placeholders before `docker compose config --quiet`; this
stack deliberately fails configuration validation when required production
secrets are missing. `--wait` waits for the declared service health checks and
the migration service's successful completion. The public `/healthz` response
is `{"status":"ok"}` and deliberately contains no configuration details.

The final `docker compose down` is the normal shutdown command. It stops and
removes containers and networks while preserving the database volume.

## Database migrations

Migrations are applied automatically by the one-shot `migrate` service using:

```text
alembic -c /app/alembic.ini upgrade head
```

It starts only after PostgreSQL is healthy. The API does not start unless that
service completes successfully. Inspect migration output with the diagnostic
log command below; do not edit migration history, run a downgrade, or manually
alter the database as a first recovery action. The migration chain is in
[`apps/api/alembic/versions`](../../apps/api/alembic/versions), and Alembic
requires the `DATABASE_URL` environment variable as shown in
[`alembic/env.py`](../../apps/api/alembic/env.py).

Before changing a migration or attempting a rollback, preserve logs and
escalate. Use an approved, validated backup-and-restore procedure for the
target environment; this repository does not provide a backup or restore
script.

## Stop and restart

Use normal Compose shutdown for either stack; it preserves the corresponding
named PostgreSQL volume. Restart with the same `up --build -d --wait` command
used to start it, then repeat the appropriate host health check.

`docker compose down -v` is different: it deletes the corresponding named
database volume (`postgres_data` for the production-style configuration or
`postgres_test_data` for the deterministic configuration, with any Compose
project prefix). It requires an explicit data-destruction decision. Do not use
it to troubleshoot a failed start, migration, login, or analysis.

## Deterministic demo stack

Use this isolated stack to evaluate the application without GitHub or AI
credentials. It sets `ENVIRONMENT=e2e`, uses the fixture GitHub source, and
does not configure an AI provider. Its data is synthetic and it is separate
from the production-style named database volume.

```bash
docker compose -f compose.test.yaml config --quiet
docker compose -f compose.test.yaml up --build -d --wait
curl --fail --silent --show-error http://127.0.0.1:4173/healthz
docker compose -f compose.test.yaml down
```

The final command is non-destructive with respect to `postgres_test_data`. The
test-only E2E bootstrap and fixture source are implementation details intended
for this deterministic stack, not a live GitHub sign-in path; see
[`main.py`](../../apps/api/src/release_intelligence/main.py) and
[`e2e.py`](../../apps/api/src/release_intelligence/api/routes/e2e.py).

## Diagnostics

For a production-style local stack, collect state and bounded logs before any
restart or configuration change:

```bash
docker compose ps --all
docker compose logs --no-color --timestamps --tail=200 postgres migrate api web
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).read().decode())"
```

For the deterministic stack, add `-f compose.test.yaml` after every `docker
compose` invocation above. Treat logs as sensitive operational material: do not
paste tokens, OAuth callback query parameters, private keys, database URLs, or
raw provider responses into tickets.

| Symptom | Boundary and first safe action |
| --- | --- |
| `docker compose config --quiet` fails, or a required variable is reported unset | **Configuration validation.** Correct `.env` syntax, the required values, and the selected port. Do not expose the file while sharing diagnostics. |
| `postgres` is unhealthy | **PostgreSQL health.** Preserve `postgres` logs and confirm the named volume remains intact. Stop normally, correct the host or database condition, then restart. |
| `migrate` exits non-zero and `api` never starts | **Migration failure.** Preserve `migrate` and `postgres` logs. Do not delete the volume, edit migration state, downgrade, or apply ad hoc SQL; escalate before a migration change. |
| Host health check or in-container API health check fails after migration succeeds | **API health.** Compare `api` logs with the configuration values and dependency status. Correct the cause, restart, and verify `/healthz` again. |
| Analysis reports GitHub authorization, incomplete data, or `github.rate_limited` | **GitHub authorization/rate limit.** Verify the App installation, repository scope, and read-only permissions. Wait until the returned reset time for a rate limit, then start a new analysis; do not reuse a partial snapshot. |
| GitHub login callback returns an authorization or upstream error | **OAuth callback.** Confirm the exact callback URL, client ID/secret, browser origin/port, and upstream query-string log suppression. Preserve only redacted evidence; do not weaken state, cookie, or CSRF protections. |
| Optional explanation is unavailable | **Optional AI failure.** The checked-in Compose stacks do not support an AI provider. In a separate non-Compose deployment that explicitly injects AI settings, check whether the provider is intentionally disabled and whether its required key/prices are configured. The deterministic assessment remains authoritative; do not treat an explanation outage as a readiness result. |

## Recovery procedures

Use this order for a failed local run:

1. Preserve the bounded service logs and `docker compose ps --all` output.
2. Stop with `docker compose down` (or the test-stack equivalent) without
   deleting volumes.
3. Correct configuration or restore upstream availability, such as PostgreSQL,
   GitHub App access, OAuth availability, rate-limit reset, or optional AI
   availability in a separately configured non-Compose deployment.
4. Restart with `up --build -d --wait`, then verify the relevant `/healthz`
   endpoint and service status.
5. Perform a new analysis run; do not reuse partial state or a result gathered
   before the failure.

If a recovery would require deleting a volume, changing a migration, rotating a
shared credential, weakening a security control, or accepting a release risk,
stop and escalate first.

## Credential rotation

Stop before rotating any GitHub App private key or OAuth client secret. Escalate
to the credential owner and obtain explicit approval for the rotation plan.
Only after that approval, follow the owner's directed plan: preserve redacted
operational evidence, update the provider credential and matching deployment
secret through the approved secret channel, then restart and verify health and
an authorized new analysis. Do not commit or log old or new credentials.

`ARI_CREDENTIAL_ENCRYPTION_KEY` has a different recovery boundary: existing
OAuth credentials in PostgreSQL were encrypted with the current Fernet key.
This repository provides no key-rewrapping procedure. Escalate before rotating
it so the owner can approve a migration or a controlled re-authentication plan;
do not erase the database merely to make a new key work.

## Data retention and cleanup

Normal shutdown preserves data:

```bash
docker compose down
docker compose -f compose.test.yaml down
```

The repository has no implemented scheduled retention, backup, or restore
workflow. Keep application data according to the owning environment's approved
policy and use only a tested external backup/restore process for that
environment.

The CI helper [`ops/compose_cleanup.sh`](../../ops/compose_cleanup.sh) invokes
`down -v --remove-orphans` for its isolated test stack. It is deliberately
destructive and is not a normal developer or evaluator cleanup command. Before
any `down -v`, identify the target Compose file and volume, confirm that its
database can be permanently deleted, and obtain the explicit
data-destruction decision. After deletion, the only supported recovery is an
approved backup/restore process; this repository does not supply one.

## Escalation conditions

Escalate before any of the following:

- deleting volumes or otherwise destroying database data;
- rotating a shared credential or the credential-encryption key;
- changing, downgrading, or otherwise intervening in an Alembic migration;
- weakening a security control, including OAuth state, session, CSRF, GitHub
  permission scope, or callback query-string log handling; or
- accepting a release risk, partial GitHub state, stale evidence, or an
  `INSUFFICIENT_DATA`/`NEEDS_DECISION` result as a release decision.

For suspected credential exposure, stop sharing logs, preserve only redacted
evidence, notify the credential owner through the approved incident channel,
and let that owner direct rotation and recovery.
