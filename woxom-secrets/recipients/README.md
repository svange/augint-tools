# Recipients

This directory contains age public key files.

- `team-<name>.txt` - Team-wide recipients (all projects)
- `project-<name>.txt` - Project-specific additional recipients

Format: one age public key per line, optionally preceded by `# username`.

These files are managed by `ai-tools team-secrets <team> admin` commands.
Do not edit manually unless you also regenerate `.sops.yaml`.
