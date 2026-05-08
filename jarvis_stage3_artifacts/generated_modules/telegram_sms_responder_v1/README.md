# Telegram SMS Responder v1

Managed additive module for Telegram text-message intake and supervised response routing.

## Current state
- local API works on port 8110
- preview requests are forwarded into Supervisor `/api/respond`
- polling bot exists as a separate process
- env-file loading is supported via `config/module.local.env`

## To enable real Telegram replies
1. Fill `config/module.local.env`
2. Start API
3. Start bot