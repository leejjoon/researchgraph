# 006: Edge inheritance on supersede is declared per edge type

Status: accepted · 2026-10-19 · amends 003

## Decision
Each edge type declares `inherit: both | outgoing | none`. When node X is superseded by X′, each non-retired edge incident to X is handled by its type's value. An edge is *transferable* when its type is `both`, or its type is `outgoing` and the edge's `from` is X. A transferable edge is retired and, when `inherit_edges` is true, recreated with X′ in X's place. Every other edge (an incoming edge of an `outgoing` type, any edge of a `none` type, every `supersedes` edge) is left untouched: it stays non-retired and keeps pointing at the now-retired X. `retire` and `reject`, which have no replacement, still retire every non-retired edge.

Core values: `blocks`, `tests`, `answers`, `part-of`, `addresses`, `instructs`, `duplicate-of`, `relates-to` are `both`. `cites`, `derived-from`, `supports`, `contradicts`, `discovered-from`, `explains`, `about`, `pins` are `outgoing`. `supersedes` is `none`. Profile-added edge types default to `outgoing`.

## Why
Decision 003 said supersede recreates every edge from the replacement. Taken literally, a Claim that cites Result R is rewritten to cite R′ when R is superseded, so the claim now rests on evidence it never examined and `stale` can never be true. The same holds for any evidence or provenance edge: the relation is about the specific node that was evaluated. Structural edges (blocking, containment, which prediction a task addresses) do carry over, because the replacement takes the old node's place in the plan.

Retiring the old `supersedes` edge on repeated supersession broke `history` and `stale` for three-generation chains, so those edges are preserved.

## Alternatives
- Two fixed classes (structural versus evidence) in the core: same effect for core types, but profiles add edge types and need somewhere to declare the choice.
- `inherit_edges=false` as the only escape: still retires provenance edges, which destroys evidence traversal.

## Consequences
- Edge table in `data-model.md` gains an `inherit` column; `EdgeType` gains the attribute.
- After a hypothesis is superseded, its replacement shows no supporting claims until someone re-asserts them with `link` (decision 007). That is the intended behaviour: evidence is re-linked explicitly. The old claims still point at the retired hypothesis, so `stale` and `evidence` keep working.
- A three-generation supersession scenario is added to the acceptance suite.
- Rejecting or retiring a proposed replacement X′ retires `supersedes X′ → X` like any other edge, so claims citing X stop being stale while X stays retired with no live replacement. This is intended: the supersession was withdrawn. `history` still follows the retired edge.
