# Skill installation and local lifecycle

Start with the [README prompt](README.en.md#quick-start) or the [Agent installation guide (Chinese)](../AGENT_INSTALL.md). This page documents release verification and the directory lifecycle.

Use only the versioned `bilibili-meme-atlas-vX.Y.Z.zip` asset from a published release. A hosting provider's generated source archive is not the deterministic Skill archive. Bootstrap also requires a source checkout at the exact repository and resolved tag commit that passed the installable-release check; run `scripts/install.py` from that checkout, never from an archive extracted before verification. Before installation, complete the checks in [RELEASING.md](RELEASING.md): the repository, resolved tag target, release notes, asset names, sizes, and trusted SHA-256 digests must match the approved local release object.

Set paths explicitly; the parent directory must already exist. `SKILL_DIR` is the verified checkout. `APPROVED_ARCHIVE_DIGEST` is the archive digest returned by the successful installable-release comparison, not a digest calculated from an otherwise untrusted download.

```bash
SKILL_DIR=/path/to/bilibili-meme-atlas
ARCHIVE=/path/to/bilibili-meme-atlas-vX.Y.Z.zip
PARENT=/existing/skill-parent
APPROVED_ARCHIVE_DIGEST=the_verified_64_character_digest

python3 "$SKILL_DIR/scripts/install.py" install "$ARCHIVE" \
  --sha256 "$APPROVED_ARCHIVE_DIGEST" \
  --parent "$PARENT"
```

The fixed target is `$PARENT/bilibili-meme-atlas`. Installation checks the trusted archive digest, fixed root, `SKILL.md` entry, portable member paths, regular file types, namespace conflicts, and size limits. Cache, VCS, and pending corpus-update members are rejected. Staged and installed files must match every member and byte of that verified archive. Source, corpus, adapter, rights, and projection checks run when preparing the corresponding release.

## Host discovery and use

The [Agent installation guide (Chinese)](../AGENT_INSTALL.md) provides the complete agent-executed flow. Agent Skills specifies the format inside a skill directory. Discovery locations, project or user scope, reload steps, and explicit invocation syntax belong to the host.

Choose `--parent` from the current host's documentation. A host supporting project-scoped `.agents/skills` can use:

```text
<project>/.agents/skills/bilibili-meme-atlas/
├── SKILL.md
├── scripts/
├── references/
└── … (all other files from the complete release)
```

Confirm that the host discovers and loads the installed skill, then use a natural-language request:

```text
Use bilibili-meme-atlas to explain “金坷垃”, appropriate usage, and the sources recorded in the local corpus.
```

The host may also select the skill from a matching task description. Verify directory installation, host discovery, and task completion separately. Follow the host's reload or new-session instructions when needed. See [compatibility (Chinese)](COMPATIBILITY.md#agent-skills-与宿主适配) for the optional `agents/openai.yaml` adapter.

## Identity-bound upgrade or rollback

Obtain the exact current-directory identity, then install another verified release archive while binding the replacement to that identity. Identity uses stable relative file paths, lengths, and content digests independently of the current source file list, adapter wording, or projection generator. Added files change the identity; extra empty directories are rejected. This same operation can roll back to a previously published release. The replaced directory remains as a hidden backup beside the target.

```bash
CURRENT_IDENTITY=$(python3 "$SKILL_DIR/scripts/install.py" identity \
  "$PARENT/bilibili-meme-atlas" | python3 -c 'import json,sys; print(json.load(sys.stdin)["identity"])')

python3 "$SKILL_DIR/scripts/install.py" install "$ARCHIVE" \
  --sha256 "$APPROVED_ARCHIVE_DIGEST" \
  --parent "$PARENT" \
  --expected-current "$CURRENT_IDENTITY"
```

## Uninstall

Uninstall moves the exact installed tree out of the discovery name and preserves it; it does not delete the tree.

```bash
CURRENT_IDENTITY=$(python3 "$SKILL_DIR/scripts/install.py" identity \
  "$PARENT/bilibili-meme-atlas" | python3 -c 'import json,sys; print(json.load(sys.stdin)["identity"])')

python3 "$SKILL_DIR/scripts/install.py" uninstall \
  --parent "$PARENT" \
  --expected-current "$CURRENT_IDENTITY"
```

## Interrupted-operation recovery

Install, upgrade, uninstall, corpus update, and recovery on the installed target share one writer lease. Read the retained operation first. Confirm that other installers, corpus updates, and editors have stopped. Bind rollback recovery to both the record digest and the exact backup identity; recovery never overwrites an existing target.

```bash
python3 "$SKILL_DIR/scripts/install.py" recovery-info --parent "$PARENT"
python3 "$SKILL_DIR/scripts/install.py" identity "$PARENT/.bilibili-meme-atlas.backup-BACKUP_ID"

python3 "$SKILL_DIR/scripts/install.py" recover \
  --parent "$PARENT" \
  --expected-recovery RECORD_DIGEST \
  --backup "$PARENT/.bilibili-meme-atlas.backup-BACKUP_ID" \
  --expected-backup BACKUP_IDENTITY \
  --acknowledge-stopped-writers
```

The full failure, lease, already-restored, and manual-backup rules are in the [Chinese lifecycle contract](INSTALL.md).

If `recovery-info` reports `committed_cleanup_pending`, the installed or removed result has already passed exact-identity readback and only the live lock cleanup remains. Recheck that state, omit backup arguments, and run:

```bash
python3 "$SKILL_DIR/scripts/install.py" recover \
  --parent "$PARENT" \
  --expected-recovery RECORD_DIGEST \
  --acknowledge-stopped-writers \
  --finalize-committed
```

Successful operations may report `cleanup_pending` when an already-retired cleanup directory could not be physically removed. It no longer owns the live lock name and does not authorize deletion without checking the reported path.
