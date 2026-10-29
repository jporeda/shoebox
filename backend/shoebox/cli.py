"""Command line: `shoebox serve | worker | run <job> | openapi`."""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="shoebox")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the web UI and API")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("worker", help="run the background job worker")
    run = sub.add_parser("run", help="run one job synchronously (e.g. ingest, export, restore)")
    run.add_argument("job")
    run.add_argument("--params", default="{}", help="JSON params")
    sub.add_parser("openapi", help="print the OpenAPI schema")
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("shoebox.app:app", host=args.host, port=args.port)
    elif args.cmd == "worker":
        from .jobs import worker_loop

        worker_loop()
    elif args.cmd == "run":
        import logging

        from .db import migrate, session_scope
        from .jobs import enqueue, run_job
        from .models import Job

        logging.basicConfig(level=logging.INFO)
        migrate()
        job = enqueue(args.job, json.loads(args.params))
        with session_scope() as s:
            j = s.get(Job, job.id)
            j.state = "running"
            s.add(j)
        run_job(job.id)
        with session_scope() as s:
            j = s.get(Job, job.id)
            print(json.dumps({"state": j.state, "result": j.result, "error": j.error}, indent=1, default=str))
            sys.exit(0 if j.state == "done" else 1)
    elif args.cmd == "openapi":
        from .app import app

        print(json.dumps(app.openapi(), indent=1))


if __name__ == "__main__":
    main()
