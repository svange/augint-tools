# Keys

Password-encrypted age private keys for team members.

Each file `<username>.key.enc` contains a user's age private key
encrypted with their personal password using `age -p`.

## Bootstrap

To decrypt and cache your key locally:
```bash
ai-tools team-secrets woxom setup
```

The decrypted key is cached at `~/.augint-tools/keys/woxom/age-key.txt`
with 600 permissions and is never committed to any repository.
