#!/usr/bin/env python3
'''One-shot watcher: poll pod /system_stats until ready or timeout.'''
import httpx
import time
import sys
from datetime import datetime
from pathlib import Path

URL = 'https://ys2svf0d350cl2-8188.proxy.runpod.net/system_stats'
DEADLINE = time.monotonic() + 25 * 60
INTERVAL = 30
LOG = Path('state/logs/pod_readiness_watcher.log')
LOG.parent.mkdir(parents=True, exist_ok=True)

def emit(line: str) -> None:
    print(line, flush=True)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(line + '\n')

emit(f'[start] watching {URL}, deadline {DEADLINE/60:.0f}min, interval {INTERVAL}s')
attempt = 0
while time.monotonic() < DEADLINE:
    attempt += 1
    ts = datetime.now().strftime('%H:%M:%S')
    try:
        r = httpx.get(URL, timeout=10.0)
        if r.status_code == 200:
            emit(f'[{ts}] attempt {attempt}: READY (HTTP 200)')
            emit(f'Body preview: {r.text[:300]}')
            sys.exit(0)
        else:
            emit(f'[{ts}] attempt {attempt}: HTTP {r.status_code}')
    except httpx.ConnectError:
        emit(f'[{ts}] attempt {attempt}: ConnectError')
    except httpx.TimeoutException:
        emit(f'[{ts}] attempt {attempt}: Timeout')
    except Exception as e:
        emit(f'[{ts}] attempt {attempt}: {type(e).__name__}: {e}')
    time.sleep(INTERVAL)

emit('[timeout] pod not ready in 25 min')
sys.exit(2)
