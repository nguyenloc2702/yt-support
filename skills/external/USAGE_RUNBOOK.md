# Runbook: combining external skills with the local script pipeline

## Recommended order

1. **Research first**
   - Use `research-evidence-gap/SKILL.md`.
   - Build an evidence packet before asking for polished narration.
   - Keep unsupported claims in `open_gaps.md`; do not ask the writer to guess.

2. **Packaging and hook ideation**
   - Read `youtube_agent_skill/skills/yt-package/SKILL.md` for title/thumbnail promise.
   - Read `youtube_agent_skill/skills/yt-script/SKILL.md` for five hook candidates.
   - Score candidates with `youtube_agent_skill/skills/yt-script/hookscore.py`.
   - Select a hook brief, not a finished script.

3. **Local generation**
   - Feed the transcript/source material, evidence packet, selected hook brief, target duration,
     and audience into the existing `ScriptFactory`.
   - Keep VI and EN generation under the local factory so scene count, word budget, terminology,
     and TTS constraints remain consistent.

4. **Local gates**
   - Run `ScriptAuditor`.
   - Reject fact gaps, numeric conflicts, unsupported names/dates, repetition, bad language,
     and TTS problems.
   - Only an approved script proceeds to the local Crayon shot and render pipeline.

5. **After publishing**
   - Export the YouTube retention CSV.
   - Run `youtube_agent_skill/skills/yt-retention/retention.py`.
   - Store the resulting feedback as a brief for the next factory run; never rewrite the old
     published artifact in place.

## Example hook-only command

From the repository root:

```powershell
python skills/external/youtube_agent_skill/skills/yt-script/hookscore.py --hook "Your candidate hook here"
```

To score several candidates, put one hook per line in `hooks.txt` and run:

```powershell
python skills/external/youtube_agent_skill/skills/yt-script/hookscore.py hooks.txt
```

## What the external skills should return

```text
TITLE_PROMISE:
AUDIENCE:
SELECTED_HOOK:
HOOK_FORMULA:
HOOK_SCORE:
OPEN_LOOPS:
PAYOFF_PROMISE:
RESEARCH_PACKET:
ALLOWED_CLAIM_IDS:
OPEN_GAPS:
VISUAL_BEAT_NOTES:
```

This is an input brief for the local generator. It is not a substitute for the local scene JSON.

## Prompt contract for an agent

Use this instruction when orchestrating a run:

> Research the topic using the evidence-gap protocol. Do not invent facts. Build a claim ledger.
> Generate five hook candidates using the vendored yt-script formulas and score them. Return the
> top two with their formula, score, title promise, open loop, payoff, allowed claim IDs, and open
> gaps. Then use the local ScriptFactory to write the VI/EN script. Preserve all source numbers,
> dates, units, and names. Run the local auditor and return the audit report. Do not create shot
> prompts until the script passes the local gates.

## Rollback

To return to the old behavior, stop supplying the external hook/research brief and run the local
factory with its normal transcript input. No production import is required for this rollback.
