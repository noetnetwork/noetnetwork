# Reviewed source build contexts

`source_bundle.py` exports the pinned native node dependencies from Git objects and reapplies only the reviewed NOET patches. It compares tracked checkout changes to those patches before export. Untracked files, ignored build outputs, RPC cookies, and local Git metadata are not copied from the checkout.

```sh
python3 protocol/source_bundle.py --out /tmp/noet-node-context.tar
```

The command prints the archive SHA-256 and refuses to overwrite an existing artifact. It includes a Dockerfile and `NOET-SOURCE-PROVENANCE.json`, which records source commits, patch hashes, and individual exported file hashes. Symlinks must remain within the context. Timestamps and ownership in the tar are normalized so repeated exports produce the same digest.

Build the isolated laboratory image from that context:

```sh
docker build -t noet-core:lab - < /tmp/noet-node-context.tar
```

The dependency locks and licenses remain inside the source distribution. This is an internal development context, not a public branding document. Do not strip required attribution from redistributed source or binaries.

The inventory is source provenance, not a complete SBOM for the resulting image. The native Dockerfile pins its Ubuntu base by digest, but OS package repositories remain mutable. A deterministic source archive does not make the resulting binary reproducible or independently audited. Release work still requires pinned build/runtime dependencies, image-package inventory, independent rebuild comparison, artifact signing, and acceptance on each supported architecture.

## Inspect the built image

```sh
python3 protocol/release_inventory.py --image noet-core:lab --out /tmp/noet-node-image.json
```

This uses an already available local image. It resolves the tag to an immutable image ID, then records installed OS package versions and hashes of the node, CLI, wallet utility and test executable. Collection containers have no network or host mounts, use a read-only filesystem, drop capabilities, and override the normal node entrypoint. They never mine, pull an image, publish an artifact or sign it.

Keep the source archive digest, resulting image inventory and acceptance results together. This simple inventory is not an SPDX/CycloneDX SBOM, vulnerability scan, hardware attestation, or independent reproducibility proof. Transitive source-built libraries still need a full release dependency inventory.

## On-demand hosted checks

`protocol/ci/acceptance.yml` runs the same protocol, wallet, economics, MySQL-backed website tests and production build on request. Select its optional native check to build this source context and run native recovery acceptance as well. It uses pinned GitHub action commits, read-only repository permissions, no production credentials, and a one-hour limit. It does not run on push, deploy, publish, or rent a GPU.

The workflow is an inactive template. GitHub rejected installing it because the current OAuth login lacks workflow permission; no permission expansion was attempted. An authorized repository owner must copy it to `.github/workflows/acceptance.yml` before it can be dispatched. Hosted runner charges and Linux AMD64 execution are not covered by the local Linux ARM64 results. Repository owners can run it from GitHub Actions after reviewing their runner allowance. Local acceptance remains available without activating a hosted job.
