# Handoff: developing Salvage Forge with Claude Code

This repo was prototyped conversationally in claude.ai. From here on,
development moves to Claude Code on the MSI laptop (Windows, RTX 4070).
This doc is the one-time setup. After it, the workflow is: open VS Code,
talk to Claude in the panel, review diffs, commit, push.

## 0. Prerequisites (one time)

- **Git for Windows** — gitforwindows.org. Required on Windows for
  Claude Code's CLI features in the integrated terminal.
- **VS Code** — code.visualstudio.com.
- **A paid Claude plan** (Pro/Max/Team) — Claude Code is included; no
  API key needed. You sign in with your claude.ai account.
- **Python 3 + deps** for this repo: `pip install requests pillow`
- **(Optional, local AI)** Ollama from ollama.com, then:
  `ollama pull qwen3-vl:8b`
- **(Optional, matcher work)** Julia from julialang.org, then:
  `julia -e 'using Pkg; Pkg.add(["JuMP","HiGHS","JSON"])'`

## 1. Put the repo on GitHub (one time)

Easiest: install **GitHub Desktop** (desktop.github.com), sign in,
File → Add Local Repository → select this folder → Publish repository.
The existing commit history comes with it.

CLI alternative:
```
git remote add origin https://github.com/YOURUSER/salvage-forge.git
git push -u origin main
```

## 2. Install the Claude Code extension (IDE integration)

1. Open VS Code → Extensions (Ctrl+Shift+X) → search **"Claude Code"**
   → install the official Anthropic extension (verified publisher).
2. File → Open Folder → this repo. Trust the workspace when asked
   (Claude Code does not run in Restricted Mode).
3. Open any file; click the **Spark icon** in the editor toolbar (or the
   Activity Bar). First launch opens a browser sign-in to your Claude
   account.
4. That's the whole IDE integration: chat panel with inline diffs,
   @-mentions of files/selections, plan review, checkpoints (rewind
   Claude's edits from any message).

The extension bundles its own CLI for the panel. If you also want to run
`claude` in the integrated terminal (some features are CLI-first),
install the standalone CLI per docs.claude.com/en/docs/claude-code.

Claude Code automatically reads **CLAUDE.md** in the repo root — that
file is the project briefing (architecture contracts, commands, roadmap).
Keep it updated; it is how every future session "remembers" the project.

## 3. Git & GitHub integration inside Claude Code

Two layers, both worth having:

**a) Plain git (works immediately).** Claude Code drives your local git
directly — ask it to commit, branch, write commit messages, resolve
merges. Nothing to configure beyond Git for Windows.

**b) GitHub connector (issues & PRs).** GitHub's official MCP server
gives Claude access to your GitHub account (create PRs, read issues,
etc.). In a Claude Code session run:

```
claude mcp add --transport http github https://api.githubcopilot.com/mcp/
```

then run `/mcp` in the session to authenticate via OAuth. Alternative:
install the **GitHub CLI** (`gh`, from cli.github.com) and sign in with
`gh auth login` — Claude Code uses it naturally for PR/issue workflows.

(Optional, for the chat interface rather than Claude Code: claude.ai →
Settings → Connectors → Add custom connector with the same GitHub MCP
URL.)

## 4. Suggested first session

Open the Claude panel and try, in plan mode first (Shift+Tab cycles to
it — Claude proposes before touching files):

> "Read CLAUDE.md and the repo. Then add pytest unit tests for
> repair_and_parse in intake/intake.py covering: markdown fences, smart
> quotes, truncated items array, and pure garbage. Commit when green."

That task is roadmap item #1, small enough to review fully, and teaches
the whole loop: plan → diff review → accept → commit → push.

## 5. Working agreements (for you, not Claude)

- Review every diff before accepting. You are the engineer of record.
- Commit small and often; push at the end of each session.
- Never commit keys. `.gitignore` already excludes `.env` and runtime
  data (`library.json`, `inventory.csv`).
- When Claude does something you don't understand, ask it to explain —
  that's the tuition-free part of this setup.
