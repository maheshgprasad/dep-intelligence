# How SSE Works in dep-intel-ui-v1

**Simple version:** The dashboard auto-refreshes the moment IBM Bob finishes writing a file.
No refresh button. No page reload. No polling. This is done with a technology called
**Server-Sent Events (SSE)**.

---

## Table of Contents

1. [What is SSE — in plain words](#1-what-is-sse--in-plain-words)
2. [Why we need SSE here — the problem it solves](#2-why-we-need-sse-here--the-problem-it-solves)
3. [How SSE connects to IBM Bob's way of working](#3-how-sse-connects-to-ibm-bobs-way-of-working)
4. [The full data flow — step by step](#4-the-full-data-flow--step-by-step)
5. [Code walkthrough — server side (ui/server.js)](#5-code-walkthrough--server-side-uiserverjs)
6. [Code walkthrough — browser side (ui/public/index.html)](#6-code-walkthrough--browser-side-uipublicindexhtml)
7. [File and code map](#7-file-and-code-map)
8. [Visual diagram of the full system](#8-visual-diagram-of-the-full-system)
9. [How the project was built — chronological code flow](#9-how-the-project-was-built--chronological-code-flow)
10. [Frequently asked questions](#10-frequently-asked-questions)

---

## 1. What is SSE — in plain words

Imagine you subscribe to a newspaper. Once you sign up, newspapers arrive at your door
automatically. You do not have to drive to the shop and ask "any news yet?" every five minutes.

**SSE works exactly like that:**

- The **browser signs up once** by connecting to a special URL (`/api/events`).
- The **server keeps the connection open** — it never closes it.
- Whenever something happens (Bob writes a file), the **server pushes a notification**
  to the browser instantly.
- The browser **reacts** to that notification and fetches the fresh data.

```
Browser                              Server
  |                                    |
  |  GET /api/events  ─────────────>  |   ← browser subscribes once
  |  (connection stays open forever)   |
  |                                    |
  |                                    |  ← Bob writes dep_matrix.json
  |  <─── "file_updated" event ──────  |   server pushes notification
  |                                    |
  |  GET /api/output/matrix  ───────>  |   ← browser fetches fresh data
  |  <─── { packages: [...] } ───────  |
  |                                    |
  |  (tab re-renders with new data)    |
```

SSE is a web standard built into every modern browser. No extra libraries needed.
It is one-directional: **server → browser only**.

---

## 2. Why we need SSE here — the problem it solves

### The challenge

Bob (the AI) runs three tools, one at a time:

```
Bob runs analyze_dependencies   → writes output/dep_matrix.json      (takes a few seconds)
Bob runs check_package_updates  → writes output/package_updates.json (takes a few seconds)
Bob runs review_code            → writes output/code_review.json     (takes a few seconds)
```

The browser has no idea when Bob finishes each tool.

### Option A — Polling (bad)

The browser could ask the server "any updates?" every 2 seconds.

```
Browser: "Any updates yet?" → Server: "No"
Browser: "Any updates yet?" → Server: "No"
Browser: "Any updates yet?" → Server: "Yes! dep_matrix.json changed"
```

Problems with polling:
- Sends hundreds of pointless requests every minute
- Still feels slow — you might wait up to 2 seconds after Bob finishes
- Wastes bandwidth and server CPU

### Option B — SSE (what we use — good)

The server watches the `output/` directory with Node.js's built-in `fs.watch()`.
The moment a file changes, the server immediately tells the browser.

```
Bob finishes → file written to disk → fs.watch fires → SSE event sent → browser re-renders
              ←───────── entire chain takes < 100ms ─────────────────►
```

---

## 3. How SSE connects to IBM Bob's way of working

IBM Bob follows a principle called **"Evidence has to travel with the work."**

Every time Bob runs an MCP tool, it writes a JSON file to `output/` with a timestamp:

```json
{
  "meta": {
    "generated_at": "2026-06-10T14:32:05.123Z",
    "source": "bob-mcp-live"
  },
  ...
}
```

This file IS the evidence of what Bob did and when.

SSE closes the loop: the moment Bob writes that evidence file, the UI instantly shows it.
The developer watching the dashboard sees Bob's work appear in real-time, tab by tab,
as Bob completes each step of the agentic loop:

```
Orient → Bound → Plan → Act → Verify → Explain
                         ↑
                    Bob writes JSON files here
                         ↑
                    SSE delivers them to the UI here
```

The UI also shows Bob's metadata (timestamp + source) in the banner at the top of the page,
so the team always knows which Bob run they are looking at.

---

## 4. The full data flow — step by step

```
Step 1: Bob (CLI or VS Code) calls the MCP tool
        bob "run analyze_dependencies"

Step 2: MCP server executes the tool
        mcp-server/tools/analyzeDeps.js
        → fetches repos via GitHub API
        → builds dependency matrix

Step 3: MCP server writes the output file
        fs.writeFileSync("output/dep_matrix.json", JSON.stringify(result))

Step 4: Node.js fs.watch() fires on the server
        fs.watch("output/", (_, filename) => {
          if (filename === "dep_matrix.json") broadcastChange(filename)
        })

Step 5: Server broadcasts SSE event to all connected browsers
        sseClients.forEach(send => send({
          type: "file_updated",
          file: "dep_matrix.json",
          ts:   Date.now()
        }))

Step 6: Browser EventSource receives the event
        es.onmessage = async (e) => {
          const ev = JSON.parse(e.data)           // { type: "file_updated", file: "dep_matrix.json" }
          if (ev.file === "dep_matrix.json") await loadMatrix()
        }

Step 7: Browser fetches fresh data
        GET /api/output/matrix
        → server reads dep_matrix.json from disk and returns it as JSON

Step 8: Browser re-renders the Dependency Matrix tab
        (no page reload — just DOM update)
```

Total time from Bob writing the file to tab updating: **< 100 milliseconds**.

---

## 5. Code walkthrough — server side (`ui/server.js`)

### 5a. SSE endpoint — keeping the connection open

```js
// ui/server.js  lines 76–86
app.get("/api/events", (req, res) => {
  // These three headers tell the browser "this is a stream, not a normal response"
  res.setHeader("Content-Type",  "text/event-stream");  // ← tells browser: SSE stream
  res.setHeader("Cache-Control", "no-cache");           // ← no caching — must be live
  res.setHeader("Connection",    "keep-alive");         // ← keep TCP connection open

  res.flushHeaders();  // ← send headers immediately, before any data

  // Create a "send function" for this specific browser connection
  const send = (data) => res.write(`data: ${JSON.stringify(data)}\n\n`);
  //                                 ↑                               ↑↑
  //                       SSE message format                  required double newline

  // Send a "handshake" event immediately so the browser knows it's connected
  send({ type: "connected" });

  // Register this connection in our Set of active clients
  sseClients.add(send);

  // Clean up when the browser disconnects (tab closed, page navigated away)
  req.on("close", () => sseClients.delete(send));
});
```

**Key points:**
- The response **never ends** — `res.write()` keeps sending data without `res.end()`.
- `sseClients` is a `Set` — it holds one send-function per connected browser tab.
  Multiple people can have the dashboard open at once.
- When a browser tab is closed, `req.on("close")` removes it from the Set automatically.
  No memory leaks.

### 5b. File watcher — detecting when Bob writes

```js
// ui/server.js  lines 74 and 94–97

const sseClients = new Set();  // ← one entry per connected browser tab

// Make sure the output directory exists before watching it
fs.mkdirSync(OUT_DIR, { recursive: true });

// Watch the output/ directory for any file changes
fs.watch(OUT_DIR, (_, filename) => {
  if (filename?.endsWith(".json")) broadcastChange(filename);
  //             ↑
  //   optional chaining: filename can be null on some OS events — skip those
});
```

### 5c. Broadcast function — pushing to all connected browsers

```js
// ui/server.js  lines 88–91
function broadcastChange(file) {
  const event = { type: "file_updated", file, ts: Date.now() };
  sseClients.forEach(fn => {
    try { fn(event); } catch { /* ignore disconnected clients */ }
  });
}
```

`broadcastChange("dep_matrix.json")` calls every registered send-function.
Each call writes `data: {"type":"file_updated","file":"dep_matrix.json","ts":1234}\n\n`
into that browser's open response stream.

---

## 6. Code walkthrough — browser side (`ui/public/index.html`)

### 6a. Connecting to the SSE stream

```js
// ui/public/index.html  ~line 374
function connectSSE() {
  const es = new EventSource("/api/events");
  //         ↑ built-in browser API — no npm package needed

  // Turn the green dot green when connected
  es.onopen  = () => { $("liveDot").classList.remove("inactive"); };

  // If connection drops, turn dot grey and retry in 5 seconds
  es.onerror = () => { $("liveDot").classList.add("inactive"); setTimeout(connectSSE, 5000); };

  // Handle incoming events
  es.onmessage = async (e) => {
    const ev = JSON.parse(e.data);

    if (ev.type === "file_updated") {
      // Route to the right tab based on which file Bob just wrote
      if (ev.file === "dep_matrix.json")      await loadMatrix();   // Tab 1
      if (ev.file === "package_updates.json") await loadUpdates();  // Tab 2
      if (ev.file === "code_review.json")     await loadReview();   // Tab 3

      // Update the timestamp in the navbar
      $("refreshStamp").textContent = "Updated: " + new Date().toLocaleTimeString();
    }
  };
}
```

### 6b. Data fetching (triggered by SSE events)

Each `load*` function is a plain `fetch()` call. SSE just tells it **when** to run.

```js
// ui/public/index.html  ~line 248
async function loadMatrix() {
  const res = await fetch("/api/output/matrix");
  if (!res.ok) { /* show empty state */ return; }
  const d = await res.json();
  // ... render the dependency matrix table from d.packages
}
```

```js
async function loadUpdates() {
  const res = await fetch("/api/output/updates");
  // ... render the updates table from d.updates
}

async function loadReview() {
  const res = await fetch("/api/output/review");
  // ... render the code findings table from d.findings
}
```

### 6c. The live indicator dot

The small dot in the navbar (`●`) shows SSE connection state:

```js
// Green, pulsing  → SSE connected, real-time updates active
es.onopen  = () => $("liveDot").classList.remove("inactive");

// Grey, static    → SSE disconnected (server stopped, network issue)
es.onerror = () => $("liveDot").classList.add("inactive");
```

```css
/* ui/public/index.html  ~line 34 */
.live-dot         { background: var(--ibm-green); animation: pulse 2s infinite; }
.live-dot.inactive{ background: #aaa;             animation: none; }
@keyframes pulse  { 0%,100%{opacity:1} 50%{opacity:.4} }
```

---

## 7. File and code map

```
dep-intel-ui-v1/
│
├── ui/server.js                    ← SSE SERVER implementation
│   ├── const sseClients = new Set()          line 74   — registry of open connections
│   ├── app.get("/api/events", ...)           line 76   — SSE endpoint
│   │     res.setHeader("Content-Type", "text/event-stream")
│   │     sseClients.add(send)
│   │     req.on("close", () => sseClients.delete(send))
│   ├── function broadcastChange(file)        line 88   — push event to all browsers
│   └── fs.watch(OUT_DIR, ...)               line 95   — watch output/ for file writes
│
├── ui/public/index.html             ← SSE CLIENT implementation
│   ├── function connectSSE()                ~line 374 — open EventSource connection
│   │     new EventSource("/api/events")
│   │     es.onmessage → routes to loadMatrix/loadUpdates/loadReview
│   │     es.onerror   → reconnect after 5s
│   ├── <span class="live-dot">              ~line 45  — green pulsing connection dot
│   └── (async function init())             ~line 390 — calls connectSSE() on page load
│
├── output/dep_matrix.json           ← Bob writes this → SSE fires → Tab 1 refreshes
├── output/package_updates.json      ← Bob writes this → SSE fires → Tab 2 refreshes
└── output/code_review.json          ← Bob writes this → SSE fires → Tab 3 refreshes
```

---

## 8. Visual diagram of the full system

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │  IBM Bob (CLI / VS Code)                                             │
 │  "run analyze_dependencies"                                          │
 └───────────────────────────┬──────────────────────────────────────────┘
                             │  JSON-RPC 2.0 over stdio (MCP protocol)
                             ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │  MCP Server  (mcp-server/index.js)                                   │
 │  Calls runAnalyzeDeps() → builds dependency matrix                   │
 │  fs.writeFileSync("output/dep_matrix.json", ...)   ◄── writes here  │
 └───────────────────────────────────────────┬─────────────────────────┘
                                             │  file written to disk
                                             ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │  UI Server  (ui/server.js)                                           │
 │  fs.watch("output/") fires ────────────────────────────────────────► │
 │  broadcastChange("dep_matrix.json")                                  │
 │  sseClients.forEach(fn => fn({ type:"file_updated", file:"..." }))  │
 └────────────────────────────────┬─────────────────────────────────────┘
                                  │  SSE text/event-stream  (HTTP keep-alive)
                                  ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │  Browser  (ui/public/index.html)                                     │
 │  EventSource.onmessage fires                                         │
 │  ev.file === "dep_matrix.json" → await loadMatrix()                  │
 │  fetch("/api/output/matrix") → re-renders Tab 1                      │
 └──────────────────────────────────────────────────────────────────────┘
```

---

## 9. How the project was built — chronological code flow

This section explains the order in which the code was designed and why each piece
depends on the previous one.

### Phase 1 — Define the output contract first

Before writing any code, three output file schemas were fixed:

```
output/dep_matrix.json       { meta, packages, summary }
output/package_updates.json  { meta, updates, summary }
output/code_review.json      { meta, findings, summary }
```

This is the IBM Bob principle: **"Contracts before code."**
The UI developer and the MCP server developer can now work independently because they
both agreed on the shape of these files before either wrote a line of code.

### Phase 2 — Build the UI server (reads files, serves SSE)

```
ui/server.js was written first:

  1. express() setup — serve static files from ui/public/
  2. API routes — /api/output/matrix, /api/output/updates, /api/output/review
                  each just reads the JSON file from output/ and returns it
  3. SSE endpoint — /api/events
                    keeps connection open, registers client in sseClients Set
  4. fs.watch()  — watches output/ directory
                    calls broadcastChange() when any .json file is written
```

At this point the UI server was complete and testable. You could write JSON files
manually to `output/` and the dashboard would update.

### Phase 3 — Build the dashboard (HTML + JavaScript)

```
ui/public/index.html was written second:

  1. Bootstrap 5 + IBM Carbon colors — styling
  2. KPI cards at the top (repos, packages, findings)
  3. Three tab panels — each renders data from one JSON file
  4. loadMatrix() / loadUpdates() / loadReview() — fetch + render functions
  5. connectSSE() — EventSource connection to /api/events
                    routes each file_updated event to the right load* function
  6. init() — calls all three load functions on page load (in case files already exist)
              then calls connectSSE() to listen for future updates
```

### Phase 4 — Build the MCP server (writes files)

```
mcp-server/ was built third:

  1. mcp-server/tools/analyzeDeps.js  — implements runAnalyzeDeps()
  2. mcp-server/tools/checkUpdates.js — implements runCheckUpdates()
  3. mcp-server/tools/reviewCode.js   — implements runReviewCode()
  4. mcp-server/index.js              — McpServer + StdioServerTransport
                                        registers 3 tools with zod input validation
                                        connects to Bob via stdio
```

The MCP server only cares about writing the correct JSON to `output/`.
It knows nothing about the UI — the contract does that job.

### Phase 5 — Test end-to-end

```
1. Verify MCP protocol:
   echo '{"jsonrpc":"2.0",...}' | node mcp-server/index.js
   → confirms tools/list returns 3 tools

2. Run all 3 tools directly (no Bob needed):
   node --input-type=module --eval "import { runAnalyzeDeps } from ..."
   → confirms output/*.json files are written with correct schema

3. Start UI server:
   cd ui && npm start
   → confirms /api/output/matrix returns data
   → confirms SSE fires when a file is touched
```

### Why build in this order?

```
Output contract  →  UI server  →  Dashboard  →  MCP server
      ↑                               ↑
  Fixed first                 Can be tested with
  Never changes               hand-written JSON files
  (decouples teams)           before MCP server exists
```

This order means you can demo the dashboard to stakeholders on Day 1 using
mock JSON files, even before the MCP server team has written a single line of code.

---

## 10. Frequently asked questions

**Q: Why not use WebSockets instead of SSE?**

SSE is simpler and enough for this use case. SSE is one-way (server → browser).
WebSockets are two-way (server ↔ browser). We only need the server to push notifications —
the browser never needs to send data back over the stream. SSE is also automatically
reconnecting (built into the `EventSource` API), whereas WebSockets require you to
implement reconnection logic yourself.

---

**Q: What happens if the UI server restarts while Bob is running?**

The `EventSource` API in the browser will automatically try to reconnect every 5 seconds
(the default, plus our explicit retry in `es.onerror`). When it reconnects, it will call
`connectSSE()` again and subscribe to the stream. It will also call `init()` again to
reload the latest files.

---

**Q: Can multiple developers have the dashboard open at once?**

Yes. `sseClients` is a `Set` with one entry per connected browser tab.
`broadcastChange()` loops over the entire Set, so every open tab refreshes simultaneously.

---

**Q: What if a file is written but no browser is connected?**

`broadcastChange()` loops over `sseClients`. If the Set is empty, the loop does nothing.
When a browser connects later, `init()` fetches the latest files on load, so it always
shows the most recent data regardless of when it was written.

---

**Q: Does the browser need to be on the same machine as the UI server?**

No. Any machine that can reach the UI server's IP and port will work.
The SSE connection goes over normal HTTP — no special firewall rules needed beyond
what you already have for the dashboard.

---

**Q: How is the live dot status different from the Bob banner?**

| Indicator | What it shows |
|-----------|--------------|
| Green pulsing dot (navbar) | SSE connection is open — real-time updates are active |
| Bob banner (below KPIs) | Whether Bob has run yet and when the last run was |

The dot tells you about the **network connection** to the server.
The banner tells you about **Bob's last activity** (from `meta.generated_at` in the JSON files).

---

*This document was written alongside dep-intel-ui-v1 — last updated 2026-06-10*
