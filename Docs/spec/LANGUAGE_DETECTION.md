# Language Detection Enhancement

## Overview
Enhanced language detection system with intelligent file traversal to accurately identify programming languages in repositories, improving test coverage analysis and code review accuracy.

## Problem Solved
Previously, language detection relied solely on:
- Repository URL patterns (e.g., "python" in name)
- Dependency matrix data (npm packages only)

This caused many repositories to be detected as "unknown", preventing proper test coverage analysis.

## Solution: Multi-Strategy Detection

### Strategy 1: File System Traversal (Primary)
When a local repository path is available, the system now:

1. **Traverses the directory structure** (up to 3 levels deep)
2. **Identifies manifest files**:
   - `package.json`, `tsconfig.json` → JavaScript/TypeScript
   - `go.mod`, `go.sum` → Go
   - `requirements.txt`, `setup.py`, `pyproject.toml` → Python
   - `pom.xml`, `build.gradle` → Java
   - `Gemfile` → Ruby
   - `composer.json` → PHP
   - `Cargo.toml` → Rust
   - `.csproj`, `.sln` → C#
   - `CMakeLists.txt` → C++

3. **Counts source file extensions**:
   - `.js`, `.jsx` → JavaScript
   - `.ts`, `.tsx` → TypeScript
   - `.py` → Python
   - `.go` → Go
   - `.java` → Java
   - `.rb` → Ruby
   - `.php` → PHP
   - `.rs` → Rust
   - `.cs` → C#
   - `.cpp`, `.cc`, `.h`, `.hpp` → C++

4. **Determines language by priority**:
   - Manifest files take precedence
   - Falls back to most common file extension

### Strategy 2: Dependency Matrix Analysis
Checks if repository has packages in `dep_matrix.json`:
- Presence of npm packages → JavaScript/TypeScript

### Strategy 3: URL Pattern Matching (Fallback)
Analyzes repository URL for language hints:
- `python`, `-py`, `django`, `flask` → Python
- `node`, `-js`, `react`, `vue`, `angular` → JavaScript
- `golang`, `-go` → Go
- etc.

## Implementation Details

### Files Modified

#### 1. `mcp-server/tools/runCoverage.js`
- Added `traverseDirectory()` function for recursive file scanning
- Added `detectLanguageFromFiles()` for manifest and extension analysis
- Enhanced `detectLanguage()` to accept optional `repoPath` parameter
- Updated main loop to search for local repository paths in common locations:
  - `~/repos/repoName`
  - `./repoName`
  - `../repoName`
  - `/tmp/repoName`

#### 2. `ui/services/languageDetector.js`
- Added identical traversal logic for consistency
- Enhanced `detectLanguage()` with `repoPath` parameter
- Maintains same multi-strategy approach

## Benefits

1. **Accurate Detection**: Identifies language even without URL hints
2. **Better Coverage Analysis**: Enables proper test coverage commands per language
3. **Reduced "Unknown" Results**: Dramatically decreases unidentified repositories
4. **Extensible**: Easy to add new language patterns
5. **Performance**: Limits traversal depth to avoid scanning large directories

## Usage

### Automatic Detection
The system automatically attempts to find local repositories when running coverage:

```javascript
// In runCoverage.js
const language = detectLanguage(repoUrl, repoPath);
```

### Manual Detection with Path
```javascript
const { detectLanguage } = require('./languageDetector');

// With local path for best accuracy
const lang = detectLanguage('github.com/org/repo', './output', '/path/to/repo');

// Without local path (falls back to other strategies)
const lang = detectLanguage('github.com/org/repo', './output');
```

## Example Output

### Before Enhancement
```
[run_coverage] Processing hello-compliance-deployment...
[run_coverage] Detected language: unknown
```

### After Enhancement
```
[run_coverage] Processing hello-compliance-deployment...
[run_coverage] Found local repo at: /Users/user/repos/hello-compliance-deployment
[run_coverage] Detected language: javascript
```

## Supported Languages

| Language | Manifest Files | Extensions |
|----------|---------------|------------|
| JavaScript | package.json | .js, .jsx |
| TypeScript | tsconfig.json, package.json | .ts, .tsx |
| Python | requirements.txt, setup.py, pyproject.toml | .py |
| Go | go.mod, go.sum | .go |
| Java | pom.xml, build.gradle | .java |
| Ruby | Gemfile | .rb |
| PHP | composer.json | .php |
| Rust | Cargo.toml | .rs |
| C# | .csproj, .sln | .cs |
| C++ | CMakeLists.txt | .cpp, .cc, .h |

## Performance Considerations

- **Max Depth**: Traversal limited to 3 directory levels
- **Skip Directories**: Ignores `node_modules`, `.git`, `vendor`, `dist`, `build`, etc.
- **Caching**: Results can be cached in dependency matrix for future runs
- **Graceful Degradation**: Falls back to URL patterns if file access fails

## Future Enhancements

1. **Language Mixing Detection**: Identify polyglot repositories
2. **Confidence Scoring**: Return confidence level with detection
3. **Custom Patterns**: Allow user-defined language patterns
4. **GitHub API Integration**: Fetch language data from GitHub's API
5. **Machine Learning**: Train model on repository structures

## Testing

To test the enhanced detection:

1. Place repositories in common locations (`~/repos/`, `./`, etc.)
2. Run coverage analysis: `npm run bob:coverage`
3. Check logs for "Found local repo at:" messages
4. Verify language detection is no longer "unknown"

## Troubleshooting

### Still Detecting as "Unknown"
- Ensure repository has manifest files or source files
- Check if repository is in a searchable location
- Verify file permissions allow reading
- Add language hints to repository URL if needed

### Wrong Language Detected
- Check if manifest files are ambiguous (e.g., both package.json and go.mod)
- Priority is: manifests → file extensions → URL patterns
- Consider adding more specific manifest detection

---

**Made with Bob** 🤖