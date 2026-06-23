# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Layout

Use a single-context layout:

- `CONTEXT.md` at the repo root
- `docs/adr/` for architectural decisions

## Before exploring, read these

- `CONTEXT.md` at the repo root, if it exists.
- `docs/adr/`, if relevant ADRs exist.

If these files do not exist, proceed silently. Domain docs are created lazily when domain terms or architectural decisions are clarified.

## Use the glossary's vocabulary

When output names a domain concept in an issue title, refactor proposal, hypothesis, or test name, use the term as defined in `CONTEXT.md`.

If the concept is not in the glossary yet, note it as a candidate for `/domain-modeling`.

## Flag ADR conflicts

If output contradicts an existing ADR, surface that conflict explicitly rather than silently overriding it.
