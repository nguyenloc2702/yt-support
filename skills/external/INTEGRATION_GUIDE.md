# External skill integration guide

## Default mode: reference-only

The vendored skills are consulted by the agent before generation, but they do not directly
rewrite the production script. The local [`ScriptFactory`](../../services/script_factory.py:374)
remains the only production generator.

Recommended flow:

1. Run research using [`research-evidence-gap/SKILL.md`](research-evidence-gap/SKILL.md).
2. Produce a source/evidence packet with verified claims, citations, dates, units, and open gaps.
3. Use `youtube_agent_skill/skills/yt-script/SKILL.md` and `hooks.json` for hook candidates and retention beats.
4. Feed only the approved hook brief and evidence packet into the existing factory.
5. Run local audit and QA. Reject any unsupported claim or numeric conflict.
6. Persist the external snapshot identifier and research packet beside the run artifact.

## Feature flag contract

Until an explicit adapter is implemented, the external pack is reference-only. A future adapter
must use a disabled-by-default flag such as `EXTERNAL_YOUTUBE_SKILLS_ENABLED=false` and must
preserve the original prompt path when disabled.

The adapter must never:

- bypass [`ScriptAuditor`](../../services/script_auditor.py:426);
- alter source numbers, dates, names, or units;
- publish or auto-upload;
- replace the Crayon visual or TTS gates;
- silently fall back from a failed research call to invented facts.

## Rollback contract

Rollback means setting the feature flag to false or removing this `skills/external/` directory.
Because production imports do not point at this directory, the current pipeline remains intact.
Every enabled run should record `external_skill_snapshot`, `external_skill_commit`, and
`external_skill_mode` in its metadata.
