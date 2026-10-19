# fail
1. Given T1 running by agent1. When agent1 fails with summary "SNR too low", step "extract_1d", check "continuum_snr>=5", measured "min 3.1". Then status `failed`, lease null, a Failure node titled `Failure: <T1 title>` with body "SNR too low" and those fields and `explains → T1`, approvals gains `{what: fail, note: "SNR too low"}`, and T1 appears in `digest.failed_24h`.
2. Given T2 blocked by T1. When T1 fails. Then T2 stays blocked.
3. Given T1 failed. When agent1 supersedes T1 with an instruction. Then the replacement is ready and T2 becomes blocked on the replacement after edge inheritance.
4. Given T1 active. When agent1 fails. Then RuleViolation `not_running`.
5. Given the Failure node from scenario 1. Then it is not listed by `ready` (Failure is not claimable).
