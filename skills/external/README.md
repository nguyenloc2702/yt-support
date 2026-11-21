# External skills snapshots

This directory contains vendored, read-only reference snapshots from public repositories.
They are deliberately isolated from the production pipeline so they can be enabled, disabled,
or removed without changing the current script/shot engine.

## Current snapshot

- `youtube_agent_skill/`: snapshot of `Jakeschincariol/youtube-agent-skill` (MIT)
- Source: https://github.com/Jakeschincariol/youtube-agent-skill
- Intended use: hook ideation, script structure reference, title/thumbnail packaging,
  retention analysis, niche research, and post-publish feedback.

## Safety boundary

External skills may suggest hooks, beats, research questions, and retention improvements.
They must not replace the local deterministic gates in:

- `services/script_factory.py`
- `services/script_auditor.py`
- `skills/video_production/`
- `skills/shot_generation/`

No external output is publishable until it passes the local fact, numeric-consistency,
scene, language, TTS, visual, and approval gates.

## Rollback

The snapshot is not imported by production code. Rollback is therefore:

1. Stop using this directory in the agent instructions.
2. Remove `skills/external/youtube_agent_skill/`.
3. Keep the local pipeline unchanged.

For a future integration, enable it through an explicit feature flag/configuration and
record the snapshot commit SHA in the run artifact. Never silently switch generators.
