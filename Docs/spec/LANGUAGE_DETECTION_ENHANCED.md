# Enhanced Language Detection with Hybrid Strategy

## Overview

The `detect_language` MCP tool provides intelligent programming language detection for repositories using a hybrid strategy that combines API-first detection with shallow clone fallback for maximum accuracy.

## Features

### 🎯 Hybrid Detection Strategy

1. **API-First Detection** (Fast, Low Bandwidth)
   - Fetches manifest files from GitHub API
   - Checks for `package.json`, `go.mod`, `requirements.txt`, etc.
   - Returns results in ~1 second per repository
   - No disk I/O required

2. **Shallow Clone Fallback** (Accurate, Comprehensive)
   - Triggered when API detection is inconclusive
   - Clones only the latest commit (`--depth 1`)
   - Analyzes complete file structure
   - Detects multi-language repositories
   - Counts source files by extension
   - ~2-5 seconds per repository

3. **Smart Caching** (7-day TTL)
   - Results cached in `output/.language_cache.json`
   - Avoids repeated API calls and clones
   - Automatic cache invalidation after 7 days
   - Can be bypassed with `force_clone` parameter

## Supported Languages

| Language   | Manifests                                      | Extensions           |
|------------|------------------------------------------------|----------------------|
| JavaScript | `package.json`, `yarn.lock`                    | `.js`, `.jsx`, `.mjs`|
| TypeScript | `tsconfig.json`, `package.json`                | `.ts`, `.tsx`        |
| Python     | `requirements.txt`, `setup.py`, `pyproject.toml`| `.py`, `.pyw`       |
| Go         | `go.mod`, `go.sum`                             | `.go`                |
| Java       | `pom.xml`, `build.gradle`                      | `.java`              |
| Ruby       | `Gemfile`, `Gemfile.lock`                      | `.rb`, `.rake`       |
| PHP        | `composer.json`, `composer.lock`               | `.php`               |
| Rust       | `Cargo.toml`, `Cargo.lock`                     | `.rs`                |
| C#         | `.csproj`, `.sln`                              | `.cs`                |
| C++        | `CMakeLists.txt`, `Makefile`                   | `.cpp`, `.h`, `.hpp` |

## Usage

### Via MCP Tool

```javascript
// Call from Bob or MCP client
{
  "tool": "detect_language",
  "arguments": {
    "repos_file": "./repos.txt",      // Optional: defaults to repos.txt
    "output_dir": "./output",         // Optional: defaults to output/
    "force_clone": false              // Optional: bypass cache and force clone
  }
}
```

### Via Node.js

```javascript
import { runDetectLanguage } from "./mcp-server/tools/detectLanguage.js";

const result = await runDetectLanguage({
  repos_file: "./repos.txt",
  output_dir: "./output",
  force_clone: false
});

console.log(result.message);
// ✅ Language detection complete
// Analyzed 3 repositories:
// - 2 detected via API
// - 0 detected via shallow clone
// - 1 from cache
```

### Via Test Script

```bash
node test-detect-language.js
```

## Output Format

### `output/language_detection.json`

```json
{
  "meta": {
    "generated_at": "2026-06-30T05:57:51.979Z",
    "source": "bob-mcp-detect-language",
    "repos_file": "./repos.txt",
    "total_repos": 3,
    "detection_stats": {
      "api_detections": 2,
      "clone_detections": 1,
      "cached_detections": 0
    }
  },
  "repositories": {
    "owner/repo-name": {
      "primary_language": "Python",
      "language_key": "python",
      "color": "#3572A5",
      "all_languages": ["Python", "JavaScript"],
      "manifests": ["requirements.txt", "package.json"],
      "extensions": {
        ".py": 45,
        ".js": 12
      },
      "total_files": 57,
      "detection_method": "clone",
      "confidence": "high",
      "timestamp": "2026-06-30T05:57:46.313Z"
    }
  }
}
```

### `output/.language_cache.json`

```json
{
  "owner/repo-name": {
    "primary_language": "Python",
    "language_key": "python",
    "color": "#3572A5",
    "timestamp": "2026-06-30T05:57:46.313Z"
  }
}
```

## Detection Logic

### Priority Order

1. **Manifest Files** (Highest Priority)
   - `go.mod` → Go
   - `tsconfig.json` → TypeScript
   - `package.json` + `.ts` files → TypeScript
   - `package.json` → JavaScript
   - `requirements.txt` → Python
   - etc.

2. **File Extension Counts** (Medium Priority)
   - Most common extension determines language
   - Weighted by file count
   - Example: 80% `.py` files → Python

3. **Multi-Language Detection**
   - All detected languages listed in `all_languages`
   - Primary language based on highest score
   - Score = (manifest_priority × 10) + file_count

## Performance Characteristics

| Method        | Speed      | Accuracy | Bandwidth | Disk I/O |
|---------------|------------|----------|-----------|----------|
| API Detection | ~1s/repo   | Good     | <1MB      | None     |
| Clone Fallback| ~3s/repo   | Excellent| 5-50MB    | Yes      |
| Cache Hit     | <0.1s/repo | Perfect  | None      | Minimal  |

## Error Handling

The tool gracefully handles:
- **Private repositories**: Falls back to clone with authentication
- **Non-existent repositories**: Returns "Unknown" with error message
- **Network failures**: Retries with different branch names (main/master)
- **Permission errors**: Skips inaccessible directories during traversal
- **Clone failures**: Returns "Unknown" with detailed error

## Authentication

### GitHub Public Repositories
```bash
export GITHUB_TOKEN="ghp_your_token_here"
```

### GitHub Enterprise
```bash
export GHE_TOKEN="your_ghe_token_here"
```

Tokens are used for:
- API requests (higher rate limits)
- Cloning private repositories
- Accessing enterprise repositories

## Cache Management

### View Cache
```bash
cat output/.language_cache.json
```

### Clear Cache
```bash
rm output/.language_cache.json
```

### Force Refresh
```javascript
await runDetectLanguage({ force_clone: true });
```

## Integration with Existing Tools

### With `analyze_dependencies`

```javascript
// Step 1: Detect languages
await runDetectLanguage({ repos_file: "./repos.txt" });

// Step 2: Analyze dependencies (uses language info)
await runAnalyzeDeps({ repos_file: "./repos.txt" });
```

### With UI Dashboard

The UI can read `language_detection.json` to:
- Display language badges with colors
- Filter repositories by language
- Show language distribution charts
- Highlight multi-language projects

## Best Practices

1. **Run periodically**: Language detection should be refreshed weekly
2. **Use caching**: Don't force clone unless necessary
3. **Check credentials**: Ensure tokens are set for private repos
4. **Monitor performance**: API detection is preferred for speed
5. **Review results**: Check `detection_method` to understand how each repo was detected

## Troubleshooting

### Issue: All repos show "Unknown"
**Solution**: Check if `GITHUB_TOKEN` or `GHE_TOKEN` is set

### Issue: Clone fallback is slow
**Solution**: This is expected for large repos. Consider using API-only mode

### Issue: Cache not working
**Solution**: Check if `output/.language_cache.json` exists and is writable

### Issue: Wrong language detected
**Solution**: Use `force_clone: true` to trigger file-based detection

## Examples

### Example 1: Detect Languages for All Repos
```bash
node test-detect-language.js
```

### Example 2: Force Clone for Accurate Detection
```javascript
await runDetectLanguage({
  repos_file: "./repos.txt",
  force_clone: true
});
```

### Example 3: Custom Output Directory
```javascript
await runDetectLanguage({
  repos_file: "./my-repos.txt",
  output_dir: "./custom-output"
});
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    detect_language Tool                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │  Check Cache    │
                    │  (7-day TTL)    │
                    └─────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
                    ▼                   ▼
          ┌──────────────────┐  ┌──────────────────┐
          │  API Detection   │  │  Cache Hit       │
          │  (Fast)          │  │  (Instant)       │
          └──────────────────┘  └──────────────────┘
                    │
          ┌─────────┴─────────┐
          │                   │
          ▼                   ▼
    ┌──────────┐      ┌──────────────┐
    │ Success  │      │ Inconclusive │
    └──────────┘      └──────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │ Shallow Clone    │
                    │ (Accurate)       │
                    └──────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │ File Traversal   │
                    │ & Analysis       │
                    └──────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │  Update Cache    │
                    │  & Return Result │
                    └──────────────────┘
```

## Future Enhancements

- [ ] Support for more languages (Kotlin, Swift, Scala)
- [ ] Framework detection (React, Django, Spring Boot)
- [ ] Build tool detection (Webpack, Gradle, Maven)
- [ ] Language version detection (Python 3.x, Node 18.x)
- [ ] Confidence scoring improvements
- [ ] Parallel processing for faster bulk detection

---

**Made with Bob** 🤖