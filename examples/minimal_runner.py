"""Unsupported reference runner (decision 002). Targets the `tasks` profile. Polls ready
Tasks, skips any without `fields.command`, claims each, runs the command in a shell with
cwd = project root, completes with every file the command wrote under artifacts/<id>/, or
fails with the exit code as `error`. Implement after T15; keep under 80 lines."""
