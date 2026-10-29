<p align="center"><img src="../assets/cover.webp" width="100%" alt="Bilibili Meme Atlas: cultural context, provenance and natural expression" /></p>
<p align="center"><a href="../SKILL.md"><img alt="Bilibili Meme Atlas icon" src="../assets/icon.png" width="20" height="20" /></a> <a href="COMPATIBILITY.md"><img alt="Python language baseline: 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-blue.svg" /></a> <a href="../LICENSE"><img alt="Project-owned content license: MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg" /></a></p>

# BilibiliMemeAtlas

A Chinese-first catalogue for historical Bilibili memes, danmaku language, remix material, ACG, games and esports, livestream culture, and expressions that travel across communities. The offline corpus records meanings, aliases, transmission relationships, usage boundaries, and source indexes. Precise provenance and additions still require reading the relevant public sources. Runtime and development checks use the Python 3.10+ standard library.

The current project version is recorded in [`references/metadata.json`](../references/metadata.json), and the Skill name is `bilibili-meme-atlas`. This is not a census of every Bilibili meme, an account-interaction tool, a video downloader, or an unattended scraper.

## Project status and boundaries

Maturity is **0.x / pre-1.0**. The project is for users and maintainers who need offline lookup, evidence-aware explanation, and maintainable historical Bilibili meme data. The acceptance baseline is: deterministic projections match their inputs; project and corpus validation passes; the complete behavioral test suite passes; archives rebuild deterministically and verify against the source tree; factual lookup excludes `candidate` records by default; and the repository does not bundle third-party complete works without an applicable right to do so. Compatibility and breaking-change handling are described in [Compatibility (Chinese)](COMPATIBILITY.md).

In scope are offline lookup, catalogue maintenance, public-source checking, deterministic archives, and local install/recovery. Account actions, video downloading, unattended scraping, real-time popularity monitoring, and redistribution of third-party media are out of scope. Source loss, `partial`/`candidate` evidence, fixed platform endpoint availability, and changes in third-party rights remain explicit maintenance risks. Runtime remains CPython 3.10+ standard library only.

Most corpus and maintenance documentation is Chinese-first. Links below are marked when no English full-text counterpart is provided.

[中文](../README.md) · [Skill](../SKILL.md) · [Catalogue (Chinese)](../references/catalog.md) · [History (Chinese)](../references/history-map.md) · [Year index (Chinese)](../references/history-index.md) · [Sources (JSON; Chinese fields)](../references/sources.json) · [Usage (Chinese)](../references/usage.md) · [Installation](INSTALL.en.md) · [Maintenance (Chinese)](MAINTENANCE.md) · [Verification (Chinese)](VERIFICATION.md) · [Release bytes (Chinese)](RELEASING.md) · [Rights](../CONTENT_RIGHTS.md)

## Quick lookup

Replace `SKILL_DIR` with this project directory. Keep the quotes when the path contains spaces; the commands can run from another working directory.

```bash
python3 "SKILL_DIR/scripts/atlas.py" search '爷青回'
python3 "SKILL_DIR/scripts/atlas.py" get '金坷垃'
python3 "SKILL_DIR/scripts/atlas.py" stats
```

`search` supports category filters, Markdown output, and pagination. It excludes `candidate` records by default while reporting the excluded match count. `get` retains every matching sense. Unknown phrases return an empty result rather than invented provenance; no result exits with status 0, while invalid input or corpus data exits with status 2.

Without command execution, start with the [catalogue](../references/catalog.md), then open the relevant period shard and [source registry](../references/sources.json). The abbreviated catalogue is not a substitute for a complete record.

## Data and evidence

Current catalogue size and category counts are available through `stats`, [coverage and gaps](../references/coverage.md), and the [corpus summary](../references/corpus-summary.json). The knowledge cutoff describes the researched corpus boundary, not a software release date.

| Status | Use |
| --- | --- |
| `verified` | Full-text sources support the registered, limited claims; check each claim against its source. |
| `partial` | State only the supported portion and retain provenance or date gaps. |
| `candidate` | Keep as a research lead and exclude from factual retrieval by default. |

`full`, `snippet`, and `metadata` describe the registered source-reading scope. `era` is a historical index grouping, not an origin date. Query results retain risks, aliases, claim-level evidence, and source limitations.

## Public-source research and updates

Write research results to an existing directory outside the project. Network responses are leads or metadata; they do not create entries or raise evidence status automatically.

```bash
python3 "SKILL_DIR/scripts/research.py" search '金坷垃' --output /output/search.json
python3 "SKILL_DIR/scripts/update.py" fingerprint
python3 "SKILL_DIR/scripts/update.py" preview --batch /output/proposal.json
python3 "SKILL_DIR/scripts/update.py" apply --batch /output/proposal.json --write
```

Search and inspection use fixed hosts, timeouts, response limits, and no retry after refusal. Updates build and validate a complete candidate tree before writing; stale baselines, duplicate IDs, dangling sources, and weak-evidence promotion are rejected. See the [update interface](../references/refresh.md) and [safety boundary](../references/safety.md).

## Local engineering entry points

```bash
python3 scripts/build.py --check
python3 scripts/validate.py
python3 scripts/verify.py
python3 scripts/package.py build --output /existing-directory/bilibili-meme-atlas.zip
python3 scripts/package.py verify /existing-directory/bilibili-meme-atlas.zip
```

The [installation guide](INSTALL.en.md) covers deterministic archives, identity-bound install/upgrade/removal, and interruption recovery. The [release byte contract](RELEASING.md) covers provider-neutral comparisons of source, notes, archives, and already-obtained release data. These local tools do not perform remote publication.

[Contributing (Chinese)](../CONTRIBUTING.md) · [Collaboration (Chinese)](../CODE_OF_CONDUCT.md) · [Security (Chinese; English contact)](../SECURITY.md) · [Compatibility (Chinese)](COMPATIBILITY.md) · [Dependencies (Chinese)](DEPENDENCIES.md)

## License

[MIT](../LICENSE) covers project-owned code, original writing, catalogue arrangement, cover, and icon. External videos, articles, phrases, music, people, and trademarks retain their own rights; the project license does not transfer them. See [content rights](../CONTENT_RIGHTS.md).
