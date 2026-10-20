# Model artifact verification

A registered name and revision identify a model. They do not prove that the files on a GPU host match the approved weights. `noet.model_artifacts` creates a deterministic file inventory and verifies every file against an independently pinned manifest digest. It never downloads weights, executes model code or spends GPU funds.

## Prepare a candidate

Use a clean directory containing the exact model snapshot, tokenizer and configuration. Store the manifest outside that directory. The revision must be a full immutable 40-character commit, not a branch or tag.

```sh
PYTHONPATH=protocol python3 -m noet.model_artifacts create /models/approved-snapshot \
  --model organization/model --revision FULL_40_CHARACTER_COMMIT \
  --license REVIEWED_LICENSE_IDENTIFIER --out /manifests/model.json
```

The command prints the candidate manifest SHA-256. A candidate is not an approval: review its source, license, revision and contents independently, and store the approved digest with the release configuration. Never accept a new digest merely because a worker supplies it.

## Verify on each worker and verifier

```sh
PYTHONPATH=protocol python3 -m noet.model_artifacts verify /models/approved-snapshot \
  /manifests/model.json --sha256 APPROVED_64_CHARACTER_MANIFEST_DIGEST
```

The check requires the complete file set, exact lengths and SHA-256 digests. Missing, changed and additional files fail verification. Symlinks, special files, unsafe paths, duplicate fields, mutable revisions and malformed manifests are rejected. Files are streamed with bounded memory; an inventory is limited to 4,096 files and 1 TiB. Files modified while being read are rejected.

Use a read-only model mount after validation and verify again on restart. A successful check proves the inspected bytes matched the manifest at that moment. It cannot prevent a privileged process from replacing files afterward, validate a model license, certify the inference engine, or prove useful work. The guarded development launcher now calls this verifier before starting a local model and requires read-only mounts. See [MINER-RUNTIME.md](MINER-RUNTIME.md). The production GPU image and real-work network are not released.

No approved production model manifest is included. The routine unit-test fixtures are deliberately tiny files, not usable model weights. A separate CPU experiment now includes a candidate manifest for a real pinned Qwen3-0.6B snapshot; all ten vendor files were downloaded and verified, and its BF16 conversion generated legacy inference evidence. See [cpu/README.md](cpu/README.md) for the measured replay differences and the explicit separation from production approval. Weights are not committed.
