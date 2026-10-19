# Astroharness (context only)

Astroharness is the first consumer of researchgraph: an astronomy-specific package that adds a profile (Sample, Dataset, Reduction, Analysis, Needs-observation node types; approval gates for hypotheses, samples, reduction departures and claim promotion), a formula library (reviewed procedure directories with parameterised notebooks and locked likelihoods), a runner that starts coding-agent sessions for ready nodes, and a web application that embeds the researchgraph viewer.

The full proposal lives as a Claude Doc: https://claude.ai/code/artifact/fd667ea2-1122-4dff-ba68-53e2972093f8

What it needs from researchgraph, and when:

| Astroharness need | researchgraph operation | Needed by |
| --- | --- | --- |
| Runner claims ready nodes with a lease, completes or fails them | claim, release, complete, fail, ready | v0.1 (astroharness month 3) |
| Human gates on hypothesis activation, sample approval, reduction departures, claim promotion | profile gates + approve/reject | v0.1 |
| Re-run with instructions | supersede + instruct | v0.1 |
| Claim-drift audit | stale, evidence, cites edges | v0.1 queries |
| Agents reach the graph through MCP | MCP server | v0.2 |
| Web app embeds the graph view, inspector, review queue, live dashboard | viewer component + change events | v0.2 |
| Expand with the prediction protocol | expand (creates Expansion task; astroharness runner executes the hook) | v0.3 |

Nothing in this repository may import from or special-case astroharness.
