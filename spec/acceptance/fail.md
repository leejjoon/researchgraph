# fail
1. Given T1 running by agent1. When agent1 fails with step "extract_1d", check "continuum_snr>=5", measured "min 3.1". Then status `failed`, lease null, a Failure node with those fields and `explains → T1`, and T1 appears in `digest.failed_24h`.
2. Given T2 blocked by T1. When T1 fails. Then T2 stays blocked.
3. Given T1 failed. When agent1 supersedes T1 with an instruction. Then the replacement is ready and T2 becomes blocked on the replacement after edge inheritance.
