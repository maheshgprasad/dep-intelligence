# Dependency Intelligence Dashboard - Setup Guide

This guide will help you configure and test the Dependency Intelligence Dashboard for your environment.

## Prerequisites

- Node.js (v18 or higher)
- npm
- Git
- GitHub token(s) with appropriate access
- Bob CLI (for MCP server integration)
- Python 3.8+ with pip (for the Code Graph and Commit Hotspots features)

## Step 1: Clone and Install

```bash
# Clone the repository
git clone <your-repo-url>
cd dep-intelligence

# Install MCP server dependencies
cd mcp-server
npm install
cd ..

# Install UI dependencies
cd ui
npm install
cd ..
```

## Step 2: Configure Environment Variables

### Option A: Automated Setup (Recommended)

Use the provided setup script to automatically configure your environment:

```bash
./setup-env.sh
```

The script will:
- Automatically detect your project directory
- Prompt for GitHub tokens
- Create a properly configured `.env` file with correct absolute paths
- Create the output directory if needed
- Verify the configuration

### Option B: Manual Setup

If you prefer to configure manually:

#### 2.1 Create .env file

```bash
cp .env.example .env
```

#### 2.2 Edit .env with your settings

Open `.env` in your text editor and configure the following:

#### Required: GitHub Tokens

Generate tokens at:
- IBM GHE: https://github.ibm.com/settings/tokens
- Public GitHub: https://github.com/settings/tokens

Required scopes: `repo` (full control of private repositories)

```bash
GHE_TOKEN=ghp_your_actual_ibm_ghe_token
GITHUB_TOKEN=ghp_your_actual_public_github_token
```

#### Required: Project Paths

Replace `/absolute/path/to/dep-intelligence` with your actual project path.

**IMPORTANT:** Use absolute paths for all variables. Do NOT use `${PROJECT_ROOT}` syntax in the values.

**macOS/Linux example:**
```bash
PROJECT_ROOT=/Users/yourname/projects/dep-intelligence
REPOS_FILE=/Users/yourname/projects/dep-intelligence/repos.txt
OUTPUT_DIR=/Users/yourname/projects/dep-intelligence/output
```

**Windows example:**
```bash
PROJECT_ROOT=C:/Users/yourname/projects/dep-intelligence
REPOS_FILE=C:/Users/yourname/projects/dep-intelligence/repos.txt
OUTPUT_DIR=C:/Users/yourname/projects/dep-intelligence/output
```

#### Required for Code Graph: CRG paths

```bash
# Path to the code-review-graph binary (inside its venv)
CRG_BIN=/absolute/path/to/.crg-venv/bin/code-review-graph

# Python interpreter in the same venv — required for hubs/bridges AND commit history
CRG_PYTHON=/absolute/path/to/.crg-venv/bin/python3
```

> **Finding the right paths:**
> ```bash
> which code-review-graph          # if on PATH
> realpath $(which code-review-graph)   # resolve symlinks
> ```
> `CRG_PYTHON` must be in the **same virtual environment** as `CRG_BIN`.

#### Optional: Commit history time window

```bash
# How many days back to analyse during automatic graph builds (default: 365 = 1 year).
# The UI lets you override this per-run from the Commit Hotspots tab.
COCHANGE_SINCE_DAYS=365
```

#### Optional: UI Port

```bash
PORT=3002  # Change if port 3002 is already in use
```

### 2.3 Verify .env configuration

```bash
# Check that .env exists and is not empty
cat .env | grep -v '^#' | grep -v '^$'
```

## Step 3: Configure Repository List

Edit `repos.txt` to include the repositories you want to analyze:

```bash
# Example repos.txt content
https://github.ibm.com/your-org/repo1
https://github.ibm.com/your-org/repo2
https://github.com/public-org/repo3
```

**Format rules:**
- One repository URL per line
- Supports both github.ibm.com and github.com
- Lines starting with `#` are ignored (comments)
- Empty lines are ignored

## Step 4: Test Configuration

### 4.1 Test Environment Variables

```bash
# Load .env and verify variables are set
source .env
echo "PROJECT_ROOT: $PROJECT_ROOT"
echo "REPOS_FILE: $REPOS_FILE"
echo "OUTPUT_DIR: $OUTPUT_DIR"
echo "GHE_TOKEN: ${GHE_TOKEN:0:10}..." # Shows first 10 chars only
```

### 4.2 Test MCP Server (Manual)

```bash
cd mcp-server

# Test analyze_dependencies
node -e "import('./tools/analyzeDeps.js').then(m => m.runAnalyzeDeps({ repos_file: '../repos.txt', output_dir: '../output' }).then(r => console.log(JSON.stringify(r, null, 2))))"

# Test check_package_updates
node -e "import('./tools/checkUpdates.js').then(m => m.runCheckUpdates({ output_dir: '../output' }).then(r => console.log(JSON.stringify(r, null, 2))))"

# Test review_code
node -e "import('./tools/reviewCode.js').then(m => m.runReviewCode({ repos_file: '../repos.txt', output_dir: '../output' }).then(r => console.log(JSON.stringify(r, null, 2))))"

cd ..
```

### 4.3 Test Automated Scan Script

```bash
# Make script executable
chmod +x run-dep-intel-scan.sh

# Run full analysis
./run-dep-intel-scan.sh
```

Expected output:
- ✅ Environment validated
- ✅ Analyzed X repositories, found Y packages
- ✅ Found Z updates
- ✅ Found W code issues
- ✅ All output files generated successfully

### 4.4 Test UI Server

```bash
cd ui
npm start
```

Then open http://localhost:3002 in your browser.

Expected behavior:
- Dashboard loads successfully
- Three tabs visible: Dependencies, Updates, Code Review
- Data loads from output JSON files
- No console errors

### 4.5 Test Bob MCP Integration

```bash
# In Bob CLI, try these commands:
bob "analyze dependencies"
bob "check for package updates"
bob "review code quality"
```

Expected behavior:
- Bob connects to MCP server successfully
- Tools execute and generate output files
- Bob reports summary statistics

## Step 5: Verify Output Files

After running any analysis, verify output files are created:

```bash
ls -lh output/
```

Expected files:
- `dep_matrix.json` - Dependency matrix
- `package_updates.json` - Package updates
- `code_review.json` - Code review findings
- `test_coverage.json` - Test coverage
- `language_detection.json` - Language detection results
- `cve_analysis.json` - CVE findings from GitHub Issues
- `security_release_report.json` / `.md` - Security release report
- `vulnerabilities.json` - Vulnerabilities (if scan_vulnerabilities tool used)
- `api_dependencies.json` - API dependencies (if detect_api_dependencies tool used)
- `repo_graphs.json` - Code graph manifest (if build_repo_graph tool used)
- `graphs/` - Per-repo SQLite knowledge graph databases
- `graphs/<slug>/cochange.json` - Commit history / co-change analysis per repo
- `cochange_index.json` - Index of all co-change analysis results

## Step 6: Install PyDriller (for Commit Hotspots)

The **Commit Hotspots** tab in the Code Graph page mines git commit history to surface
file churn scores and hidden co-change coupling. It requires the `pydriller` Python package
installed in the **same virtual environment** as `CRG_BIN`.

```bash
# Install into the CRG venv (use the real path from your .env CRG_PYTHON)
/absolute/path/to/.crg-venv/bin/pip install pydriller

# Verify
/absolute/path/to/.crg-venv/bin/python3 -c "import pydriller; print(pydriller.__version__)"
```

Expected output: `2.x.x` (any version ≥ 2.0 works).

> **Why the same venv?** The UI server uses `CRG_PYTHON` as the interpreter for both the
> hubs/bridges analysis and the co-change script. Using the same venv means one install
> satisfies both requirements.

Once installed, use the **Analyse Commit History** button on the Code Graph page to run
the first analysis. Results persist in `output/graphs/<slug>/cochange.json` and are loaded
automatically the next time you open the **Commit Hotspots** tab.

## Troubleshooting

### Issue: "401 Unauthorized" errors

**Cause:** Invalid or expired GitHub token

**Solution:**
1. Verify token is correct in `.env`
2. Check token has `repo` scope
3. For IBM GHE, ensure token is from github.ibm.com
4. Regenerate token if expired

### Issue: "repos.txt not found"

**Cause:** Incorrect REPOS_FILE path in `.env`

**Solution:**
1. Verify `REPOS_FILE` path in `.env` is correct
2. Ensure `repos.txt` exists at that location
3. Use absolute paths to avoid confusion

### Issue: "Cannot write to output directory"

**Cause:** Output directory doesn't exist or lacks write permissions

**Solution:**
```bash
# Create output directory
mkdir -p output

# Fix permissions (Unix/Linux/macOS)
chmod 755 output
```

### Issue: "Port already in use"

**Cause:** Another process is using port 3002

**Solution:**
1. Change `PORT` in `.env` to an available port (e.g., 3003, 8080)
2. Or stop the process using port 3002:
```bash
# Find process using port 3002
lsof -i :3002

# Kill the process (replace PID with actual process ID)
kill -9 PID
```

### Issue: "pydriller is not installed"

**Cause:** PyDriller is missing from the Python environment pointed to by `CRG_PYTHON`.

**Solution:**
```bash
# Install pydriller into the CRG venv
$(grep CRG_PYTHON .env | cut -d= -f2 | tr -d '"') -m pip install pydriller

# Or with the explicit path:
/absolute/path/to/.crg-venv/bin/pip install pydriller
```

### Issue: Commit Hotspots tab shows "Co-change data not yet built"

**Cause:** The analysis has not been run yet, or was run before `pydriller` was installed.

**Solution:**
1. Ensure `pydriller` is installed (see above).
2. Click **Analyse Commit History** in the Code Graph action bar, or click **Build / Update Graphs** (which runs co-change automatically).
3. Wait for the progress modal to complete — co-change runs after the CRG graph build.

### Issue: Co-change shows 0 commits for a recent time window

**Cause:** The repository genuinely had no source-file changes in that window, or the time
window is too narrow for the repo's commit cadence.

**Solution:**
1. Switch the **Time window** dropdown to a wider range (e.g., "All time").
2. Click **Reload** to re-fetch with the new window.
3. If you want to permanently change the default window, set `COCHANGE_SINCE_DAYS` in `.env`
   and restart the UI server.

### Issue: Bob MCP server not connecting

**Cause:** Incorrect PROJECT_ROOT in `.env` or Bob config

**Solution:**
1. Verify `PROJECT_ROOT` in `.env` matches your actual project path
2. Restart Bob CLI to reload configuration
3. Check `.bob/config.json` uses `${PROJECT_ROOT}` variable

### Issue: "Module not found" errors

**Cause:** Dependencies not installed

**Solution:**
```bash
# Reinstall MCP server dependencies
cd mcp-server
rm -rf node_modules package-lock.json
npm install
cd ..

# Reinstall UI dependencies
cd ui
rm -rf node_modules package-lock.json
npm install
cd ..
```

## Configuration Examples

### Example 1: macOS user analyzing IBM GHE repos

```bash
# .env
GHE_TOKEN=ghp_abc123xyz789...
GITHUB_TOKEN=
PROJECT_ROOT=/Users/johndoe/projects/dep-intelligence
REPOS_FILE=/Users/johndoe/projects/dep-intelligence/repos.txt
OUTPUT_DIR=/Users/johndoe/projects/dep-intelligence/output
PORT=3002
```

### Example 2: Windows user analyzing public GitHub repos

```bash
# .env
GHE_TOKEN=
GITHUB_TOKEN=ghp_def456uvw012...
PROJECT_ROOT=C:/Users/janedoe/projects/dep-intelligence
REPOS_FILE=C:/Users/janedoe/projects/dep-intelligence/repos.txt
OUTPUT_DIR=C:/Users/janedoe/projects/dep-intelligence/output
PORT=3002
```

### Example 3: Custom paths and port

```bash
# .env
GHE_TOKEN=ghp_abc123xyz789...
GITHUB_TOKEN=ghp_def456uvw012...
PROJECT_ROOT=/opt/dep-intelligence
REPOS_FILE=/opt/dep-intelligence/config/my-repos.txt
OUTPUT_DIR=/var/dep-intelligence/results
PORT=8080
```

**Note:** All paths must be absolute. The `.env` file does not support variable expansion like `${PROJECT_ROOT}`.

### Example 4: Full Code Graph + Commit History setup (macOS)

```bash
# .env
GHE_TOKEN=ghp_abc123xyz789...
GITHUB_TOKEN=
PROJECT_ROOT=/Users/johndoe/projects/dep-intelligence
REPOS_FILE=/Users/johndoe/projects/dep-intelligence/repos.txt
OUTPUT_DIR=/Users/johndoe/projects/dep-intelligence/output
CRG_BIN=/Users/johndoe/.crg-venv/bin/code-review-graph
CRG_PYTHON=/Users/johndoe/.crg-venv/bin/python3
COCHANGE_SINCE_DAYS=365
PORT=3002
```

Install PyDriller once after setting up the venv:
```bash
/Users/johndoe/.crg-venv/bin/pip install pydriller
```

## Next Steps

Once setup is complete:

1. **Regular Analysis**: Run `./run-dep-intel-scan.sh` periodically to update data
2. **Dashboard Monitoring**: Keep UI server running to monitor real-time updates
3. **Bob Integration**: Use Bob CLI for interactive analysis and code review
4. **Automation**: Set up cron jobs or CI/CD pipelines to run scans automatically

## Support

For issues not covered in this guide:
1. Check project documentation in README.md
2. Review AGENTS.md for Bob governance rules
3. Check individual tool documentation in mcp-server/tools/
4. Review UI_AUTOMATION_GUIDE.md for dashboard features

---

**Made with Bob** 🤖