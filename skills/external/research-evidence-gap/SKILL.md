---
name: research-evidence-gap
version: 1.0.0
status: local-reference-only
description: Research protocol for documentary and business videos when source information is incomplete.
---

# Research and Evidence-Gap Skill

## Purpose

Use this skill before script generation when the transcript, brief, or user-provided facts are
insufficient. Its job is to produce an auditable evidence packet, not to write plausible facts.

## Non-negotiable truth states

Every claim must be placed in exactly one state:

- `FACT`: directly supported by a primary source or a source explicitly supplied by the user.
- `CORROBORATED`: supported by at least two independent reputable sources, with citations.
- `LEAD`: a useful search result or secondary report that still needs verification.
- `GAP`: unknown, contradictory, paywalled, stale, or unsupported.
- `INFERENCE`: an interpretation clearly labeled as interpretation, never presented as fact.

Never convert `LEAD`, `GAP`, or `INFERENCE` into narration as if it were `FACT`.

## Research workflow

1. Define the exact question and the decision it supports in the video.
2. Break the topic into atomic claims: who, what, when, where, amount, unit, comparison,
   cause, consequence, and source date.
3. Prefer sources in this order:
   - company filings, government databases, court records, standards bodies, and official releases;
   - audited financial statements and investor presentations;
   - reputable financial/news outlets with named sources;
   - academic research and industry reports;
   - search snippets, social posts, and unsourced summaries only as leads.
4. For each claim, capture the exact source URL, publisher, publication date, accessed date,
   quoted passage or table, page/section, entity, value, unit, currency, and period.
5. Cross-check every number. Normalize currency, scale, date range, and denominator before
   comparing values.
6. Resolve conflicts by preserving both values with their definitions and dates. Do not choose
   the more dramatic number without evidence.
7. Mark unresolved items as `GAP` and either remove them from the script or write a transparent
   sentence that says the information is not established.
8. Build a source-to-claim ledger and an explicit list of research questions still open.
9. Only after the evidence packet is complete, create a hook and narrative outline.

## Required evidence packet

```json
{
  "topic": "...",
  "as_of": "YYYY-MM-DD",
  "research_questions": [],
  "claims": [
    {
      "id": "C001",
      "claim": "...",
      "state": "FACT|CORROBORATED|LEAD|GAP|INFERENCE",
      "entity": "...",
      "value": "...",
      "unit": "...",
      "period": "...",
      "sources": [
        {
          "url": "...",
          "publisher": "...",
          "published": "YYYY-MM-DD",
          "accessed": "YYYY-MM-DD",
          "locator": "page/section/table",
          "quote": "..."
        }
      ],
      "conflicts": [],
      "allowed_for_narration": true,
      "notes": "..."
    }
  ],
  "open_gaps": [],
  "comparison_definitions": [],
  "research_log": []
}
```

## Script handoff rules

- Pass only claims with `allowed_for_narration=true` to the script generator.
- Preserve claim IDs in outline notes so each important sentence can be audited.
- Any number, date, name, market share, revenue, valuation, ranking, or causal claim without a
  source is a hard failure.
- If the brief asks for an answer that cannot be verified, ask for a source or state the gap.
- The hook may be dramatic in framing, but not dramatic by changing evidence.
- After VI and EN generation, compare both versions against the same claim ledger.

## Useful research output for the local pipeline

Produce these artifacts next to the run:

- `research/evidence_packet.json`
- `research/source_notes.md`
- `research/open_gaps.md`
- `research/claim_ledger.csv`

The local [`ScriptAuditor`](../../../services/script_auditor.py:426) and quality gates remain
mandatory after handoff.
