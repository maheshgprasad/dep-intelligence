# Dependency Intelligence Scan — Bob Skill & Automation

Automated workflow for cross-repository dependency analysis, code quality review, CVE scanning, and code graph exploration.

## Overview

The dep-intel project includes two automation paths:

1. **Shell Script** — `./run-dep-intel-scan.sh` — runs the core MCP analysis tools in sequence
2. **Bob Skills** — auto-invocable skills registered in `.bob/skills/`

### Available Skills

| Skill | Trigger | Purpose |
|-------|---------|---------|
| `setup-code-graph` | "set up code graph", "enable code graph", "configure CRG" | Walks through installing `code-review-graph`, setting `CRG_BIN`/`CRG_PYTHON` in `.env`, building graphs, and verifying all 8 Code Graph tabs |

### Invoking a skill

```
set up code graph
```
or explicitly:
```
/setup-code-graph
```

## Quick Start

### Method 1: Using the Shell Script (Recommended)

```bash
./run-dep-intel-scan.sh
```

This single command will:
- ✅ Validate environment (`.env` and `repos.txt`)
- ✅ Load environment variables automatically
- ✅ Run `detect_language`, `analyze_dependencies`, `check_package_updates`, `review_code`, `run_coverage`, `scan_cve_from_github_issues` in sequence
- ✅ Verify output files were generated
- ✅ Display a formatted summary report

### Method 2: Ask Bob

```
Bob, run dependency scan
```
or
```
Bob, scan all repos
```

## Prerequisites

### Required Files

1. **`.env`** - Environment variables file containing:
   ```env
   # GitHub tokens
   GHE_TOKEN=your_github_enterprise_token
   GITHUB_TOKEN=your_github_token

   # Project paths (use absolute paths — no variable expansion)
   PROJECT_ROOT=/absolute/path/to/dep-intelligence
   REPOS_FILE=/absolute/path/to/dep-intelligence/repos.txt
   OUTPUT_DIR=/absolute/path/to/dep-intelligence/output

   # UI server port
   PORT=3002

   # Code Graph (required for dep-graph.html)
   CRG_BIN=/absolute/path/to/.venv/bin/code-review-graph
   CRG_PYTHON=/absolute/path/to/.venv/bin/python3
   ```

   **Important:** All paths must be absolute. Do not use `${PROJECT_ROOT}` syntax.

   **See [SETUP_GUIDE.md](./SETUP_GUIDE.md) for detailed configuration instructions.**
   **For `CRG_BIN` and `CRG_PYTHON` setup, ask Bob: `"set up code graph"`.**

2. **`repos.txt`** - List of repository URLs (one per line):
   ```
   https://github.ibm.com/ZaaS/zcrypto-service-manager
   https://github.ibm.com/ZaaS/zcrypto-servicebroker
   https://github.ibm.com/ZaaS/zcrypto-cloudtke
   https://github.com/zhmcclient/zhmccli
   https://github.ibm.com/ZaaS/iaas_configuration
   https://github.ibm.com/cloudlab/platform-inventory
   ```

### System Requirements

- Node.js >= 18.0.0
- `jq` command-line JSON processor (for formatted output)
- Valid GitHub/GHE authentication tokens

## Output Files

All output files are generated in the `output/` directory:

### Code Graph files

| File/Dir | Purpose |
|----------|---------|
| `repo_graphs.json` | Manifest of all built graphs with stats and build timestamps |
| `graphs/<slug>/graph.db` | SQLite knowledge graph for each repo |

### Analysis files

### 1. `dep_matrix.json`
Dependency matrix showing all packages across repositories:
```json
{
  "meta": {
    "generated_at": "2026-06-16T10:56:09.123Z",
    "total_repos": 5,
    "total_packages": 0
  },
  "common_packages": [],
  "unique_packages": [],
  "version_mismatches": []
}
```

### 2. `package_updates.json`
Available package updates classified by severity:
```json
{
  "meta": {
    "generated_at": "2026-06-16T10:56:21.456Z",
    "total_updates": 0
  },
  "updates": {
    "major": [],
    "minor": [],
    "patch": []
  }
}
```

### 3. `code_review.json`
Code quality findings:
```json
{
  "meta": {
    "generated_at": "2026-06-16T10:56:36.789Z",
    "total_findings": 0
  },
  "findings": {
    "dead_code": [],
    "undefined_variables": []
  }
}
```

## Workflow Steps

The automated workflow executes the following steps:

### Step 1: Environment Validation
- Checks for `.env` file existence
- Checks for `repos.txt` file existence
- Exits with error if either is missing

### Step 2: Analyze Dependencies
- Loads environment variables from `.env`
- Fetches manifest files from all repositories
- Builds cross-repo dependency matrix
- Identifies common packages and version mismatches
- Writes `output/dep_matrix.json`

### Step 3: Check Package Updates
- Reads dependency matrix from previous step
- Queries package registries for latest versions
- Classifies updates as major, minor, or patch
- Writes `output/package_updates.json`

### Step 4: Review Code Quality
- Fetches source files from repositories
- Scans for dead code patterns
- Detects undefined variables
- Writes `output/code_review.json`

### Step 5: Verify Outputs
- Confirms all three JSON files were created
- Displays file sizes and modification times
- Exits with error if any file is missing

### Step 6: Generate Summary
- Extracts key metrics from all output files
- Displays formatted summary report
- Lists next steps for remediation

## Example Output

```
╔════════════════════════════════════════════════════════════╗
║     Dependency Intelligence Scan - Automated Workflow     ║
╚════════════════════════════════════════════════════════════╝

[1/6] Validating environment...
✅ Environment validated

[2/6] Analyzing dependencies across repositories...
✅ Analyzed 5 repositories, found 0 packages

[3/6] Checking for package updates...
✅ Found 0 updates: 0 major, 0 minor, 0 patch

[4/6] Reviewing code quality...
✅ Found 0 issues: 0 dead code, 0 undefined variables

[5/6] Verifying output files...
✅ All output files generated successfully

[6/6] Generating summary report...

╔════════════════════════════════════════════════════════════╗
║              Dependency Intelligence Summary              ║
╚════════════════════════════════════════════════════════════╝

📊 Repositories Analyzed: 5
📦 Total Packages: 0
🔄 Updates Available: 0 (0 major, 0 minor, 0 patch)
🔍 Code Issues Found: 0 (0 dead code, 0 undefined vars)

📁 Output Files:
   • output/dep_matrix.json - Dependency matrix
   • output/package_updates.json - Package updates
   • output/code_review.json - Code review findings

✅ Scan complete! Review the output files for detailed findings.
```

## Troubleshooting

### Error: .env file not found
**Solution:** Create a `.env` file in the project root with required tokens:
```bash
cp .env.example .env
# Edit .env and add your tokens
```

### Error: repos.txt file not found
**Solution:** Create `repos.txt` with repository URLs (one per line)

### Error: Command 'jq' not found
**Solution:** Install jq:
```bash
# macOS
brew install jq

# Ubuntu/Debian
sudo apt-get install jq
```

### Limited repositories analyzed (e.g., only 1 of 6)
**Solution:** Ensure `GHE_TOKEN` is set in `.env` for IBM GHE repositories

### No packages found
**Possible causes:**
- Repositories don't contain standard manifest files
- Repositories are empty or archived
- Token doesn't have read access to repositories

## Governance

This skill follows the governance rules defined in [`AGENTS.md`](AGENTS.md):

- ✅ **Read-only mode** - No modifications to source repositories
- ✅ **Approved actions** - All operations are pre-approved
- ✅ **Evidence contract** - Generates timestamped JSON outputs
- ✅ **Credential safety** - Tokens loaded from environment only

## Integration with Bob

### Skill Activation Keywords

The skill can be activated using any of these phrases:
- "scan dependencies"
- "dep intel"
- "analyze repos"
- "check updates"
- "review code"
- "run dependency scan"
- "scan all repos"

### Skill Configuration

The skill is configured in [`../../.bob/skills/dep-intel-scan.json`](../../.bob/skills/dep-intel-scan.json) with:
- Workflow steps definition
- Input/output specifications
- Permission requirements
- Environment validation rules

## Next Steps After Scan

1. **Review Findings**
   - Open output files in the dashboard UI
   - Prioritize critical updates and security issues

2. **Address Issues**
   - Update packages with security vulnerabilities
   - Fix code quality issues identified
   - Resolve version mismatches across repos

3. **Document Changes**
   - Update CHANGELOG.md
   - Create Jira tickets for major updates
   - Notify team of breaking changes

4. **Re-scan**
   - Run scan again after fixes
   - Verify issues are resolved
   - Track progress over time

## Related Documentation

- [`README.md`](README.md) - Project overview
- [`AGENTS.md`](AGENTS.md) - Bob governance rules
- [`SSE_EXPLAINED.md`](SSE_EXPLAINED.md) - MCP server architecture
- [`.env.example`](.env.example) - Environment variables template

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Review the MCP server logs in `output/mcp_logs`
3. Consult the AGENTS.md governance document
4. Contact the ZaaS team

---

**Last Updated:** 2026-06-16  
**Version:** 1.0.0  
**Maintained by:** Bob (IBM AI Assistant)