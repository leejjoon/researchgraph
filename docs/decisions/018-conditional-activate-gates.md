# 018: Conditional activation gates use immutable materialised fields

Status: accepted · 2026-10-21

## Decision

Extend `Gate` with `when_fields: dict | None = None`. A gate matches when
its type matches and every condition equals the corresponding normalised,
default-materialised immutable field. Conditions are exact scalar matches:
booleans, strings and numbers retain their JSON types (a boolean is not a
number); missing keys never match. Conditions cannot contain null, arrays or
objects. An empty mapping is an unconditional conjunction. No coercion or
expression evaluation occurs.

On profile load, reject conditions on `node_type="*"`, unknown direct property
keys, non-mappings and non-scalar values with `schema`. Keep the existing
`unsupported_gate_transition` refusal for transitions other than `activate`.
All matching gates must be satisfied under the existing role/agent rules.
The existing mixed-gate permission boundary is retained: if any matching gate
is human-only, agent approval is refused, even if another matching gate admits
agents. Nonmatching gates do not participate in that decision.

Every node constructor (`propose`, `supersede`, `expand`, `instruct`, `fail`,
including an Instruction made by supersede) evaluates its own materialised
fields. A matching gate creates a proposed node; otherwise it is active.
Supersession re-evaluates gates and never inherits approvals. Claiming before
activation remains `not_ready`. Queue and approval checks use the same match.

## Rationale and alternatives

A profile can require review for exceptional work without requiring it for
every instance of a type. A consumer-side check could be bypassed through
another operation door. An expression language or mutable condition would
add execution/reevaluation semantics that immutable scalar conjunctions avoid.
Run and completion gates remain deferred, as in decision 009.

## Consequences

No node-file or approval-record format changes. Existing unconditional gates
and bundled profiles retain their behaviour. Tests cover defaults, all node
constructors, multiple gates, roles, agents and file/index parity. Discipline
specific conditions belong in downstream profiles, not the core.
