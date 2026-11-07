# Development

## Layout

```
backend/    FastAPI app (Python ≥ 3.10), tests, demo data generator, dev server
frontend/   React + Vite + Tailwind v4 + shadcn/ui (new-york, zinc)
deploy/     The YAML users paste into TrueNAS
spike/      Checks run on a real NAS before building on them, with their results
docs/       Plan, UI brief, design handoff, review findings
.github/    Workflow: tests, then the image on ghcr.io
```

How it works, in depth: [CLAUDE.md](../CLAUDE.md) (architecture and gotchas) and
[PLAN.md](PLAN.md) (constraints and design decisions).

## Run it locally

Backend, once:

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

A simulated dataset with snapshots, and its settings in `dev/demo/snapshots-browser.env`:

```bash
backend/.venv/bin/python backend/scripts/make_demo.py dev/demo
```

The API on :8080. It replaces the SMB sign-in with `demo` / `demo` and reads files as you, with no
privilege drop:

```bash
cd backend && set -a && . ../dev/demo/snapshots-browser.env && set +a && .venv/bin/python scripts/dev_server.py
```

The frontend on :5173, proxying `/api` to :8080:

```bash
cd frontend && npm install && npm run dev
```

## Tests

```bash
cd backend && .venv/bin/pytest
```

The privilege-drop tests only run as root; CI runs the suite a second time with `sudo`.

```bash
cd frontend && npm run lint && npx tsc -b
```

## Releases

Every push to `master` publishes `ghcr.io/fredsimard/snapshots-browser:edge`. A tag `vX.Y.Z` publishes
`:X.Y.Z`, `:X.Y` and `:latest`. Before tagging, set the version in `backend/pyproject.toml` and the
`image:` line of `deploy/snapshots-browser.yaml`.
