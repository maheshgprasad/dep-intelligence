# Documentation Directory Enforcement Skill

## Overview

This repository includes a Bob skill that automatically enforces documentation organization by ensuring all documentation files are created in the `Docs/` directory.

## Skill Location

The skill is defined in: `.bob/skills/enforce-docs-directory.md`

## What It Does

The skill automatically:
1. **Redirects documentation creation** - When you or Bob attempt to create documentation outside the `Docs/` directory, it will be created in `Docs/` instead
2. **Informs users** - Provides clear feedback when paths are corrected
3. **Maintains consistency** - Ensures all project documentation follows the same organizational structure

## Activation

The skill activates automatically when:
- Creating new documentation files (*.md, *.txt, *.rst, *.adoc)
- Generating guides or reports
- Moving or reorganizing documentation
- Any task involving documentation creation

## Rules

### Documentation Must Be in Docs/
All documentation files must be placed in the `Docs/` directory, with one exception:
- ✅ `Docs/SETUP_GUIDE.md` - Correct
- ✅ `Docs/features/API_DOCS.md` - Correct (subdirectories allowed)
- ✅ `README.md` - Correct (root README is the only exception)
- ❌ `SETUP_GUIDE.md` - Incorrect (will be moved to Docs/)
- ❌ `docs/guide.md` - Incorrect (wrong case, should be Docs/)

### Supported File Types
- Markdown files (*.md)
- Text files (*.txt)
- reStructuredText (*.rst)
- AsciiDoc (*.adoc)
- Guides (*_GUIDE.md)
- Changelogs (CHANGELOG.*)
- Contributing guides (CONTRIBUTING.*)

## Examples

### Example 1: Creating a New Guide
**Request**: "Create a deployment guide"
**Result**: File created at `./Docs/DEPLOYMENT_GUIDE.md`

### Example 2: User Specifies Wrong Path
**Request**: "Create API documentation at ./api-docs.md"
**Bob's Action**: 
- Creates file at `./Docs/API_DOCS.md`
- Informs user: "I'll create the API documentation in the Docs directory as per this repository's documentation policy."

### Example 3: Multiple Files
**Request**: "Create setup and usage guides"
**Result**: 
- `./Docs/SETUP_GUIDE.md`
- `./Docs/USAGE_GUIDE.md`

## Benefits

1. **Consistency** - All documentation in one predictable location
2. **Discoverability** - Easy to find all project documentation
3. **Maintainability** - Simpler to manage and update documentation
4. **Automation** - No manual enforcement needed

## For Repository Users

When working with this repository:
- Always create documentation in the `Docs/` directory
- Bob will automatically correct paths if you forget
- Use subdirectories within `Docs/` for better organization
- The root `README.md` is the only exception to this rule

## For Bob

When processing documentation tasks:
- Always prefix documentation paths with `Docs/`
- Inform users when correcting paths
- Create the `Docs/` directory if it doesn't exist
- Allow subdirectories within `Docs/` for organization