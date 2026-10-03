# Setup

Python 3.11 or newer, and Node.js 18 or newer.

```bash
skills/install-mcp-pydriller/scripts/install.sh
cd web
npm install
```

That script installs the MCP server (`mcp` and the `dep-intel-mcp` command) and PyDriller into `server/.venv`.

Start the dashboard and the API together:

```bash
./scripts/nami-trace.sh
```

Open http://127.0.0.1:3002 and use **Run analysis**. That one process serves the Carbon app and the analysis API.

## Repositories

`repos.txt` accepts a local directory or an `https://` Git repository URL, one per line. Lines starting with `#` are ignored.

Copy `.env.example` to `.env` when you add GitHub URLs.

- `GITHUB_TOKEN` for github.com
- `GHE_TOKEN` for any other GitHub host, including github.ibm.com

The API and the MCP server both read `.env` from the repository root. Existing environment variables win over the file.

Analysis shallow-clones each GitHub URL with `git clone --depth 1` into `output/work`, reads that tree for language, dependencies, APIs, review, and coverage, writes the JSON reports under `output/`, and deletes the checkout when the pass finishes. A local path in `repos.txt` is read in place and is not deleted. Code-graph checkouts under `output/checkouts` stay so later graph queries can reopen them.

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
