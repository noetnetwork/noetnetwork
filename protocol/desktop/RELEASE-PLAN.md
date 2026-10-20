# Native wallet release plan

## Current state

The Linux Qt native signing development build uses the isolated laboratory chain and test coins. A separate watch-only web companion and unpacked Chrome development package now exist under `wallets/` and `web/public/wallet-app/`. They track public addresses only and do not sign, create keys or recover wallets. A separate laboratory native-messaging bridge now exists in `wallets/native/`, with a standalone Tk desktop interface, explicit native approval, separate sign-only and send actions, and encrypted backup export. It supports the pinned laboratory and private-work networks. Native Windows/macOS installers, mobile signing apps and a consumer signing extension are not released. The website distinguishes the companion from those planned releases.

## Delivery order

1. Desktop: keep the native Core wallet as the signing authority. The Tk interface and native approval windows now pass headless Linux tests; complete real-node and installed-platform acceptance, then produce and test Windows and macOS packages. Sign installers, publish checksums and provenance, and test upgrades without changing or losing wallet data.
2. Mobile: implement receive, QR scanning, transaction review, send, lock and recovery. Establish which native transaction and key types are supported before implementing the signer. Protect encrypted wallet material using platform-backed storage; test locked-device and backup restoration behavior.
3. Chrome extension: add application connections only once there is a supported native signer and an application protocol. Separate the page-facing provider, permission service and signing process. Require an explicit review for each signing request.

## Shared behavior

- Identify the chain by its genesis hash, not a ticker or a website URL. Refuse wrong-network addresses and transactions.
- Define a versioned, encrypted recovery format and supported key/descriptor types. Prove compatibility through test vectors and restoration across supported platforms before claiming that one backup works everywhere.
- Do not label a Bitcoin seed phrase, Ethereum account or Privy identity as a NOET recovery method without native compatibility tests. The current wallet uses Core descriptors and encrypted wallet backups.
- Keep private signing material on the user's device. Portal authentication and explorer queries must not receive private keys, recovery data or passphrases.
- Review recipients, amounts, change, fees and network before signing. Do not silently sign an opaque payload.
- Use an authenticated node connection or a separately reviewed public broadcast service. A read-only explorer is not a signing or wallet-custody service.

## Extension boundary

Application requests need a versioned schema, explicit origin permissions, bounded payloads, unique request identifiers and replay protection. Revoke permissions per site. Never accept a page-provided origin as authoritative; derive it from the browser sender. The content script must not have access to encrypted vault material or decrypted keys. Opening a connection must not imply approval to sign.

## Acceptance before downloads

- Restore encrypted backups on a clean machine and confirm addresses and spend authority.
- Test interrupted writes, failed upgrades, wrong passphrases, locked-wallet signing and corrupted backups.
- Reject wrong-chain, malformed, unsupported and misleading signing requests.
- Test fees, change detection and exact integer amounts against the native node.
- Verify package signatures and update rollback behavior on each target OS.
- Complete independent review of key handling and native transaction signing.
- Validate real proof-of-useful-work networking separately. A working wallet does not establish production consensus or mining readiness.

No store listing, signed installer or mainnet availability is implied by this plan.
