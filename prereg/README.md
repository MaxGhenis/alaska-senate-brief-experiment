# Timestamps

`i86 prereg` writes `SHA256SUMS`, the SHA-256 of the registration (`README.md`), the map, the four
briefs and their hashes, the response template, the context brief, the assignment, `pyproject.toml`,
the package and the scripts. Once runs exist, it adds every run's `meta.json`, `response.md`,
`response.json`, `audit.json` and `transcript.jsonl`, a Sol run's `codex-sessions/` folder, and the
files at the top of `outputs/`. It then stamps the manifest with OpenTimestamps. Each folder below
keeps one manifest and its proof; `SHA256SUMS` and `SHA256SUMS.ots` at the top hold the latest.

To check a folder, run `shasum -a 256 -c prereg/<folder>/SHA256SUMS` from the repository's root at
the commit that added the folder, and `uvx --from opentimestamps-client ots verify
prereg/<folder>/SHA256SUMS.ots` once the stamp has a Bitcoin attestation.

| Folder | Written | Covers |
|---|---|---|
