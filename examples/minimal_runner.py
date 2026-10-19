"""Unsupported reference runner (decision 002). Polls ready tasks, claims each, runs
`fields.command` in a shell, completes with any files the command wrote under
artifacts/<id>/, or fails with the exit code. Implement after T15; keep under 80 lines."""
