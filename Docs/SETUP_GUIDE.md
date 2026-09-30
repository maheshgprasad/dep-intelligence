# Setup

Python 3.11 or newer, and Node.js 18 or newer.

```bash
python3 -m venv server/.venv
server/.venv/bin/pip install -e "server[dev]"
cd web
npm install
```

Start the API:

```bash
server/.venv/bin/uvicorn dep_intel.api:app --app-dir server --port 8010
```

Start the Carbon app:

```bash
cd web
npm run dev
```

Open http://127.0.0.1:3002 and use **Run analysis**.

## Repositories

`repos.txt` accepts a local directory or an `https://` Git repository URL, one per line. Lines starting with `#` are ignored.

Copy `.env.example` to `.env` when you add GitHub URLs.

- `GITHUB_TOKEN` for github.com
- `GHE_TOKEN` for any other GitHub host, including github.ibm.com

The API and the MCP server both read `.env` from the repository root. Existing environment variables win over the file.

## Tests

```bash
server/.venv/bin/pytest server/tests
```

## Commit hotspots on the fixtures

Hotspots need a git history inside the fixture directory. From the repository root:

```bash
for dir in fixtures/auth-service fixtures/user-service fixtures/report-job; do
  git -C "$dir" init
  git -C "$dir" add -A
  git -C "$dir" -c user.email="dev@example.com" -c user.name="dev" commit -m "Initial"
done
```

Those `.git` directories are gitignored.
