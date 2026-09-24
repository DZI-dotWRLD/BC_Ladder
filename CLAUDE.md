# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

The shared agent rules live in `AGENTS.md` (also used by Codex) and are
imported here. Keep project rules there, not in this file.

@AGENTS.md

## Claude Code specifics

- Repo skills live in `.agents/skills/<name>/SKILL.md`, not `.claude/skills/`.
  Read the matching `SKILL.md` directly before specialist work.
- The shell is Windows. Use PowerShell with `$env:DJANGO_DEBUG = "true"` before
  any `manage.py` command, or prefix Bash commands with `DJANGO_DEBUG=true`.
- `scripts/quality.ps1` keeps going after a failing step. Check each step's
  output, or run the gates one by one.
- `gh` may not be installed. If it isn't, say so rather than guessing CI or PR
  state.
