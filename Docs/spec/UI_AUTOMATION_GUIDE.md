# UI Automation Guide — Automated Bob Analysis & Code Graph

This guide explains the automation features of the dep-intel-ui-v1 dashboard.

## Overview

The dashboard includes two separate pages:

- **`index.html`** (`http://localhost:3002`) — main dependency analysis dashboard
- **`dep-graph.html`** (`http://localhost:3002/dep-graph.html`) — interactive code knowledge graph explorer

Both pages share the same SSE stream (`/api/events`) for real-time updates.

### Main Dashboard Features

1. **One-Click Analysis** — Run all Bob MCP tools with a single button click
2. **Real-Time Progress Tracking** — Monitor analysis progress with live updates
3. **Language Detection** — Automatic detection and display of repository programming languages
4. **Background Execution** — Analysis runs in the background without blocking the UI

### Code Graph Features

1. **Build / Update Graphs** — Shallow-clones repos, builds CRG knowledge graphs, streams progress via SSE
2. **8 auto-loading tabs** — Flows, Communities, Hubs & Bridges, Impact Radius, Architecture, Code Quality, Refactor Intel all load on first visit per repo
3. **Symbol search** — Debounced FTS/semantic search with caller/callee drill-down
4. **Lazy tab loading** — `tabLoaded` Set prevents redundant fetches; switching repos clears the cache

## Features

### 1. Run Analysis Button

Located in the top navigation bar, the "Run Analysis" button triggers a complete dependency analysis workflow.

**What it does:**
- Executes `analyze_dependencies` tool
- Executes `check_package_updates` tool
- Executes `review_code` tool
- Updates all dashboard data automatically

**Usage:**
1. Click the green "Run Analysis" button in the navbar
2. A progress modal appears showing real-time status
3. Wait for completion (typically 30-60 seconds depending on repo count)
4. Dashboard automatically refreshes with new data

### 2. Progress Modal

The progress modal provides real-time feedback during analysis:

**Components:**
- **Progress Bar** — Visual indicator of completion percentage
- **Current Phase** — Shows which tool is currently running
- **Activity Log** — Detailed log of all operations
- **Cancel Button** — Ability to cancel the running analysis

**Phases (main dashboard analysis):**
1. Starting (10%)
2. Detecting languages (20%)
3. Analyzing dependencies (35%)
4. Checking package updates (50%)
5. Reviewing code (65%)
6. Running coverage (75%)
7. Scanning CVEs (85%)
8. Complete (100%)

**Phases (Code Graph build):**
1. Cloning repositories
2. Building graph (CRG build)
3. Post-processing (flows + communities)
4. Cleaning up clone
5. Writing manifest

### 3. Repository List with Language Badges

A new card displays all monitored repositories with automatic language detection.

**Features:**
- Repository name and full URL
- Color-coded language badge
- Language-specific icon
- Repository count badge

**Supported Languages:**
- JavaScript (yellow)
- TypeScript (blue)
- Python (blue-green)
- Go (cyan)
- Java (brown)
- Ruby (red)
- PHP (purple)
- Rust (orange)
- C# (green)
- C++ (pink)

**Detection Method:**
The system detects languages using:
1. Manifest files (package.json, requirements.txt, go.mod, etc.)
2. Repository URL patterns
3. Dependency matrix data

### 4. Automatic Updates

The dashboard uses Server-Sent Events (SSE) to provide real-time updates:

**Events:**
- `file_updated` — When output JSON files change
- `bob_status` — Analysis phase updates
- `bob_log` — Detailed log entries
- `bob_complete` — Analysis completion
- `bob_error` — Error notifications

## API Endpoints

### Code Graph endpoints

| Endpoint | Description |
|---|---|
| `POST /api/crg/build` | Start async graph build for all repos in `repos.txt`; progress streams via SSE |
| `GET /api/crg/repos` | List repos with built graphs (manifest) |
| `GET /api/crg/stats?slug=` | Node/edge/community/flow counts for a repo |
| `GET /api/crg/search?q=&kind=&slug=` | FTS/semantic symbol search |
| `GET /api/crg/callers?name=&slug=` | Callers of a symbol |
| `GET /api/crg/callees?name=&slug=` | Callees of a symbol |
| `GET /api/crg/flows?slug=` | Execution flows sorted by criticality |
| `GET /api/crg/flow?id=&slug=` | Single flow with step details |
| `GET /api/crg/communities?slug=` | Leiden-detected communities |
| `GET /api/crg/hubs?slug=` | Top 10 most-connected nodes |
| `GET /api/crg/bridges?slug=` | Top 10 highest betweenness-centrality nodes |
| `GET /api/crg/impact?name=&slug=` | Blast radius for a symbol or file |
| `GET /api/crg/architecture?slug=` | Community coupling map |
| `GET /api/crg/dead-code?slug=` | Unreferenced symbols |
| `GET /api/crg/large-functions?slug=&min_lines=` | Oversized functions/classes/files |
| `GET /api/crg/refactor-suggestions?slug=` | Remove/move suggestions |

### Main dashboard endpoints

### POST /api/bob/run-analysis

Triggers a full analysis run.

**Request:**
```bash
curl -X POST http://localhost:3002/api/bob/run-analysis
```

**Response:**
```json
{
  "success": true,
  "message": "Analysis completed successfully",
  "sessionId": "bob-session-1234567890",
  "logs": [...]
}
```

### GET /api/bob/status

Get current analysis status.

**Response:**
```json
{
  "isRunning": false,
  "sessionId": "bob-session-1234567890",
  "logs": [...]
}
```

### POST /api/bob/cancel

Cancel running analysis.

**Response:**
```json
{
  "success": true,
  "message": "Analysis cancelled"
}
```

### GET /api/languages

Get language detection results for all repositories.

**Response:**
```json
{
  "success": true,
  "languages": {
    "https://github.com/owner/repo": {
      "name": "JavaScript",
      "color": "#f1e05a",
      "icon": "bi-filetype-js"
    }
  }
}
```

## Architecture

### Backend Services

**bobCliService.js**
- Manages Bob CLI execution
- Handles session lifecycle
- Emits progress events
- Provides error handling

**languageDetector.js**
- Detects repository languages
- Supports multiple detection methods
- Provides language metadata (color, icon)

### Frontend Components

**Progress Modal**
- Real-time progress visualization
- Activity log display
- Cancel functionality

**Repository List**
- Language badge display
- Repository information
- Auto-refresh on updates

## Configuration

### Environment Variables

```bash
# Server configuration
PORT=3002
OUTPUT_DIR=/path/to/output
REPOS_FILE=/path/to/repos.txt

# GitHub tokens (optional, for live data)
GHE_TOKEN=your_github_enterprise_token
GITHUB_TOKEN=your_github_token
```

### repos.txt Format

```
https://github.com/owner/repo1
https://github.ibm.com/org/repo2
https://github.com/owner/repo3
```

## Usage Examples

### Starting the Dashboard

```bash
cd ui
npm install
npm start
```

The dashboard will be available at `http://localhost:3002`

### Running Analysis Programmatically

```javascript
// Using fetch API
const response = await fetch('http://localhost:3002/api/bob/run-analysis', {
  method: 'POST'
});
const result = await response.json();
console.log(result);
```

### Monitoring Progress via SSE

```javascript
const eventSource = new EventSource('http://localhost:3002/api/events');

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  if (data.type === 'bob_status') {
    console.log('Phase:', data.phase);
  }
  
  if (data.type === 'bob_complete') {
    console.log('Analysis complete!');
  }
};
```

## Troubleshooting

### Analysis Fails to Start

**Problem:** Clicking "Run Analysis" shows an error immediately.

**Solutions:**
1. Check that `repos.txt` exists and contains valid repository URLs
2. Verify `output/` directory is writable
3. Check server logs for detailed error messages

### Progress Modal Stuck

**Problem:** Progress modal shows but doesn't update.

**Solutions:**
1. Check SSE connection (green dot in navbar should be active)
2. Verify server is running and accessible
3. Check browser console for JavaScript errors

### Language Detection Shows "Unknown"

**Problem:** Repositories show "Unknown" language.

**Solutions:**
1. Run analysis at least once to populate dependency matrix
2. Check repository URL format
3. Add language hints to repository names if needed

### No Repositories Displayed

**Problem:** Repository list is empty.

**Solutions:**
1. Verify `repos.txt` exists and is not empty
2. Check file permissions
3. Restart the server

## Best Practices

1. **Regular Analysis** — Run analysis daily or after significant changes
2. **Monitor Logs** — Check activity logs for warnings or errors
3. **Cancel Long Runs** — Use cancel button if analysis takes too long
4. **Verify Data** — Check that all three output files are generated
5. **Keep repos.txt Updated** — Add/remove repositories as needed

## Security Considerations

1. **Token Management** — Store GitHub tokens in `.env` file, never commit
2. **Access Control** — Dashboard has no authentication, use network security
3. **Rate Limiting** — Be mindful of GitHub API rate limits
4. **Error Handling** — Sensitive data is not exposed in error messages

## Performance Tips

1. **Limit Repositories** — Start with 5-10 repos, scale gradually
2. **Use Caching** — Output files are cached, reuse when possible
3. **Monitor Resources** — Analysis can be CPU/memory intensive
4. **Optimize Network** — Use local repos when available

## Future Enhancements

Potential improvements:
- Scheduled automatic analysis
- Email notifications on completion
- Comparison with previous runs
- Export reports to PDF/CSV
- Integration with CI/CD pipelines
- Multi-user support with authentication

## Support

For issues or questions:
1. Check server logs in terminal
2. Review browser console for errors
3. Verify configuration in `.env` and `repos.txt`
4. Consult main README.md for general setup

## Related Documentation

- [README.md](./README.md) — Main project documentation
- [AGENTS.md](./AGENTS.md) — Bob governance rules
- [SSE_EXPLAINED.md](./SSE_EXPLAINED.md) — Server-Sent Events details
- [ENHANCED_FEATURES.md](./ENHANCED_FEATURES.md) — Additional features