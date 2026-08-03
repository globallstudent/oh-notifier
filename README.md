# oh-notifier

Error monitoring for Python services — buffered, deduplicated Telegram alerts
with automatic categorisation.

Only runtime dependency is `httpx`.

## Quick start

```python
import oh_notifier
from oh_notifier.integrations.fastapi import ErrorMiddleware
from oh_notifier.logging_handler import OhLoggingHandler
from oh_notifier.utils import setup_excepthooks, setup_loop_exception_handler

oh_notifier.configure(
    bot_token=settings.telegram_error_bot_token,
    chat_id=settings.telegram_error_chat_id,
    service_name="core-service",
    timezone="Asia/Tashkent",
)
await oh_notifier.start()

setup_loop_exception_handler()
setup_excepthooks()
logging.getLogger().addHandler(OhLoggingHandler())
app.add_middleware(ErrorMiddleware)
```

Note there is **no `environment=` argument above** — see below.

## Environments

The environment is resolved, in order, from:

1. an explicit `environment=` argument (unless it is the bare string
   `"development"`, which is treated as a caller's default, not a decision)
2. `OH_NOTIFIER_ENV`
3. `APP_ENV`
4. `ENVIRONMENT`
5. `ENV`

If none is set the environment is **`unknown`**, alerts are labelled
`[ENV UNKNOWN — set APP_ENV]`, and a warning is logged once at startup.

> This is deliberate. Every production alert this project sent for months read
> `[DEVELOPMENT]` — not a formatting bug, but `APP_ENV` unset on all four
> production deployments combined with a silent `"development"` default. A
> monitor that mislabels which system is on fire is worse than one that says
> nothing.

`prod`/`live` → `production`, `stage` → `staging`, `dev` → `development`.
Any other name (`demo`, `sandbox`, `qa-2`) passes through unchanged, so a new
environment needs no code change.

### Per-environment configuration

Every setting reads from `OH_NOTIFIER_<NAME>`, and an
`OH_NOTIFIER_<NAME>_<ENVIRONMENT>` variant wins when present:

```bash
OH_NOTIFIER_CHAT_ID=-1001111111111           # default chat
OH_NOTIFIER_CHAT_ID_DEMO=-1002222222222      # demo alerts go elsewhere
OH_NOTIFIER_ENVIRONMENTS=production,staging  # demo stays silent entirely
OH_NOTIFIER_MIN_LOG_LEVEL_STAGING=WARNING    # noisier in staging
```

`.env` files are loaded automatically, most specific first:
`.env.<environment>.local`, `.env.<environment>`, `.env.local`, `.env`.
Real environment variables always win — in Kubernetes the manifest is the
source of truth and a baked-in `.env` must not outrank it. Disable with
`configure(load_dotenv=False)`.

### Variables

| Variable | Default | Meaning |
|---|---|---|
| `OH_NOTIFIER_ENV` | — | environment name (beats `APP_ENV`) |
| `OH_NOTIFIER_BOT_TOKEN` | — | Telegram bot token |
| `OH_NOTIFIER_CHAT_ID` | — | destination chat |
| `OH_NOTIFIER_ENABLED` | `true` | master switch |
| `OH_NOTIFIER_ENVIRONMENTS` | all | comma-separated allowlist |
| `OH_NOTIFIER_MIN_LOG_LEVEL` | `ERROR` | level name or number |
| `OH_NOTIFIER_FLUSH_INTERVAL` | `2.0` | seconds between flushes |
| `OH_NOTIFIER_DEDUP_WINDOW` | `300.0` | seconds before a repeat is a new alert |
| `OH_NOTIFIER_MAX_PENDING_EVENTS` | `500` | hard buffer ceiling; drops are counted and reported |
| `OH_NOTIFIER_MAX_SEND_ATTEMPTS` | `3` | retries per message |
| `OH_NOTIFIER_BATCH_MESSAGES` | `true` | pack alerts into fewer API calls |
| `OH_NOTIFIER_CAPTURE_HTTP_5XX` | `true` | report 5xx responses that never raised |
| `OH_NOTIFIER_CAPTURE_HTTP_4XX` | `false` | usually client noise |
| `OH_NOTIFIER_MAX_BODY_BYTES` | `16384` | request-body bytes retained for an alert |
| `OH_NOTIFIER_SENSITIVE_KEYS` | — | extra keys to mask, comma-separated |

## Delivery model

Alerts are delivered on a dedicated daemon thread with its own event loop —
never on the application's.

`capture()` does one dict update under a short lock and sets an event. No
I/O, no awaiting, nothing that can stall a request, a Celery task or a
RabbitMQ consumer. Everything else — HTTP, retries, rate limiting — happens
on the delivery thread.

`sync_flush()` only signals that thread; it never blocks the caller.

## What gets captured

| Source | How |
|---|---|
| Unhandled HTTP exceptions | `ErrorMiddleware` |
| 5xx responses with no exception | `ErrorMiddleware` (`capture_http_5xx`) |
| WebSocket errors | `ErrorMiddleware` |
| `logging` records at `min_log_level`+ | `OhLoggingHandler` |
| Unhandled event-loop errors | `setup_loop_exception_handler()` |
| Crashes in plain threads | `setup_excepthooks()` |
| Uncaught exceptions reaching the interpreter | `setup_excepthooks()` |
| Failed `asyncio` tasks | `safe_create_task()` |
| Celery task failures and retries | `setup_celery_alerts()` |
| APScheduler job errors and misses | `setup_apscheduler_alerts()` |
| aio-pika consumer errors | `@safe_consumer_handler` |

Delivery counters are available from `oh_notifier.stats()` — safe to expose
from a health endpoint.

## Grouping

Repeats collapse into one alert with a count. The key is the error type, the
last application frame, the endpoint and status, plus a normalised message
when there is no traceback — so `order <uuid> not found` groups across ids
while genuinely different failures stay apart.

## Development

```bash
uv run pytest      # 108 tests
uv run ruff check src tests
uv build           # wheel into dist/
```

Services vendor the built wheel (`oh_notifier-<version>-py3-none-any.whl`)
and reference it from `pyproject.toml`, so a library change means rebuilding
the wheel and copying it into each service repo.
