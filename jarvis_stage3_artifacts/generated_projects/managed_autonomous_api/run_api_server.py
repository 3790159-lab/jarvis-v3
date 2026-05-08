import argparse
import os
import sys
import traceback

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stable launcher for managed_autonomous_api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--log-level", default="info")
    return parser.parse_args()

def main() -> int:
    args = parse_args()

    try:
        import uvicorn
    except Exception:
        print("[run_api_server] failed to import uvicorn", file=sys.stderr, flush=True)
        traceback.print_exc()
        return 1

    try:
        print(f"[run_api_server] cwd={os.getcwd()}", flush=True)
        print(f"[run_api_server] python={sys.executable}", flush=True)
        print(f"[run_api_server] host={args.host}", flush=True)
        print(f"[run_api_server] port={args.port}", flush=True)

        # Important:
        # - workers=1
        # - reload=False
        # - direct app import string for uvicorn
        # - this call should block until process is stopped
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=False,
            workers=1,
            log_level=args.log_level,
            access_log=True,
            lifespan="on",
        )
        return 0

    except KeyboardInterrupt:
        print("[run_api_server] interrupted", flush=True)
        return 0
    except Exception:
        print("[run_api_server] fatal startup/runtime error", file=sys.stderr, flush=True)
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
