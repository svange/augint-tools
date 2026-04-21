# woxom-secrets

Team shared secrets repository. Encrypted with SOPS + age.

## Quick Start

1. Install prerequisites: `sops` and `age`
2. Run setup:
   ```bash
   ai-tools team-secrets woxom setup --repo .
   ```
3. Edit secrets:
   ```bash
   ai-tools team-secrets woxom edit <project> --env dev
   ```
4. Sync to GitHub:
   ```bash
   ai-tools team-secrets woxom sync <project>
   ```

## Structure

- `recipients/` - Age public keys for team members
- `keys/` - Password-encrypted private keys for bootstrap
- `projects/` - Encrypted env files per project/environment
- `.sops.yaml` - Auto-generated SOPS configuration

## Administration

- Add user: `ai-tools team-secrets woxom admin add-user <name> --pubkey <key>`
- Remove user: `ai-tools team-secrets woxom admin remove-user <name>`
- Add project: `ai-tools team-secrets woxom admin init-project <name>`
- Rotate keys: `ai-tools team-secrets woxom admin rotate --all`
