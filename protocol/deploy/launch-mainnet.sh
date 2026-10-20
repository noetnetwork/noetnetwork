#!/usr/bin/env bash
# Launch the hosted noet-mainnet-1 node, attach a GPU verifier and miner, and
# switch the website. Each stage is separate and safe to re-run.
#
#   protocol/deploy/launch-mainnet.sh check SECRETS_DIR
#   protocol/deploy/launch-mainnet.sh node  SECRETS_DIR
#   protocol/deploy/launch-mainnet.sh gpu   SECRETS_DIR SSH_WRAPPER USER@HOST GPU_IP:PORT[,STANDBY_IP:PORT]
#     (for a standby verifier, run it against the standby host with the full list, first verifier first)
#   protocol/deploy/launch-mainnet.sh web   SECRETS_DIR
#
# SECRETS_DIR comes from `network_secrets.py --no-faucet`. The real allocation
# signers must be set first (`testnet/set_allocation_keys.py --network mainnet`);
# the build refuses otherwise. The GPU mines to the ecosystem vault's 2-of-3
# address, so no reward key ever sits on the GPU host. Secret values are passed
# on stdin, never on the command line.
set -euo pipefail
STAGE=${1:?stage: check, node, gpu or web}
SECRETS=$(cd "${2:?secrets directory}" && pwd)
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
APP=noet-mainnet
REGION=yyz
KEYS="$ROOT/protocol/mainnet/allocation-keys.json"
# The build export needs Python 3.12+ (tarfile extraction filters).
PY=${PYTHON:-python3.14}
read_secret() { tr -d '\n' < "$SECRETS/$1"; }
genesis() { "$PY" -c "import json;print(json.load(open('$ROOT/protocol/mainnet/network.json'))['genesisHash'])"; }
ecosystem_vault() {
  "$PY" "$ROOT/protocol/testnet/allocations.py" --vault-addresses "$KEYS" noet \
    | "$PY" -c "import json,sys;print(json.load(sys.stdin)['ecosystem'])"
}

check() {
  [[ -f "$KEYS" ]] || { echo "No mainnet signers yet: run protocol/testnet/set_allocation_keys.py --network mainnet ..."; exit 1; }
  [[ ! -e "$SECRETS/faucet-token" && ! -e "$SECRETS/faucet-passphrase" ]] \
    || { echo "This secret set has faucet secrets; mainnet has no faucet (network_secrets.py --no-faucet)"; exit 1; }
  for name in explorer-token shared-secret miner-credentials miner-rpcauth certs/node-bundle.b64 certs/gpu-bundle.b64; do
    [[ -s "$SECRETS/$name" ]] || { echo "Missing $SECRETS/$name"; exit 1; }
  done
  "$PY" "$ROOT/protocol/deploy/rotate.py" --secrets "$SECRETS" --check
  echo "Genesis: $(genesis)"
  echo "Vaults (2-of-3, no time lock):"
  "$PY" "$ROOT/protocol/testnet/allocations.py" --vault-addresses "$KEYS" noet
  echo "GPU reward address (ecosystem vault): $(ecosystem_vault)"
}

case "$STAGE" in
check)
  check
  ;;
node)
  check >/dev/null
  fly apps list --json | grep -q "\"Name\": *\"$APP\"" || fly apps create "$APP" --org personal
  # Raw TCP services (relays, P2P) need a dedicated IPv4 address.
  fly ips list --app "$APP" --json | grep -q '"Type": *"v4"' || fly ips allocate-v4 --app "$APP" --yes
  fly ips list --app "$APP" --json | grep -q '"Type": *"v6"' || fly ips allocate-v6 --app "$APP"
  fly volumes list --app "$APP" --json | grep -q noet_mainnet_data \
    || fly volumes create noet_mainnet_data --app "$APP" --region "$REGION" --size 10 --yes
  {
    echo "NOET_EXPLORER_TOKEN=$(read_secret explorer-token)"
    echo "VALIDATOR_SHARED_SECRET=$(read_secret shared-secret)"
    echo "NOET_TLS_BUNDLE=$(read_secret certs/node-bundle.b64)"
    echo "NOET_MINER_RPCAUTH=$(read_secret miner-rpcauth)"
  } | fly secrets import --app "$APP" --stage
  CTX=$(mktemp -d)/context
  "$PY" "$ROOT/protocol/deploy/fly/prepare.py" --out "$CTX" --profile mainnet >/dev/null
  (cd "$CTX" && fly deploy --remote-only --app "$APP" --strategy immediate)
  fly ips list --app "$APP"
  for _ in $(seq 1 30); do
    info=$(curl -fsS "https://$APP.fly.dev:9443/mining/info" 2>/dev/null) && break
    sleep 10
  done
  echo "$info"
  "$PY" -c "import json,sys; got=json.loads(sys.argv[1] or '{}').get('genesis'); sys.exit(0 if got==sys.argv[2] else f'Unexpected genesis {got}')" \
    "$info" "$(genesis)"
  echo "Node deployed on the mainnet genesis."
  ;;
gpu)
  check >/dev/null
  SSH=${3:?ssh wrapper}
  HOST=${4:?user@host}
  VERIFIER=${5:?verifier endpoint ip:port as reachable from Fly}
  NODE_IP=$(fly ips list --app "$APP" --json | "$PY" -c "import json,sys;print([i['Address'] for i in json.load(sys.stdin) if i['Type']=='v4'][0])")
  REWARD=$(ecosystem_vault)
  # A research Pod may carry a self-stop timer ("sleep N; runpodctl stop pod"),
  # a billing safeguard that would stop mainnet's verifier. Removing it is the
  # owner's decision, so this stage only refuses while one is armed. Every such
  # timer waits in a `sleep N` of an hour or more.
  timers=$("$SSH" "$HOST" 'for p in /proc/[0-9]*; do a=$(tr "\0" " " < $p/cmdline 2>/dev/null); case "$a" in "sleep "[0-9]*) n=${a#sleep }; n=${n%% *}; [ "$n" -ge 3600 ] 2>/dev/null && echo "${p#/proc/}";; esac; done; true')
  if [[ -n "$timers" ]]; then
    echo "This GPU host has a self-stop timer (sleep process $timers) that would stop mainnet's verifier."
    echo "Remove it only if you accept running this Pod without that safeguard, then re-run this stage."
    exit 1
  fi
  # The GPU host serves one network at a time: keep the previous configuration.
  "$SSH" "$HOST" 'set -e; L=/workspace/noet-live; mkdir -p $L/previous; cp -p $L/live.json $L/gpu-bundle.b64 $L/shared-secret $L/miner-credentials $L/previous/ 2>/dev/null || true'
  "$SSH" "$HOST" 'cat > /workspace/noet-live/gpu-bundle.b64' < "$SECRETS/certs/gpu-bundle.b64"
  "$SSH" "$HOST" 'cat > /workspace/noet-live/shared-secret' < "$SECRETS/shared-secret"
  "$SSH" "$HOST" 'cat > /workspace/noet-live/miner-credentials' < "$SECRETS/miner-credentials"
  printf '{"node": "%s", "verdictPort": 17443, "rpcPort": 18443, "rewardAddress": "%s", "network": "mainnet", "gateway": "https://%s.fly.dev:9443"}\n' \
    "$NODE_IP" "$REWARD" "$APP" | "$SSH" "$HOST" 'cat > /workspace/noet-live/live.json'
  for pair in miner/noet_miner.py:noet_miner.py gpu/live.py:live.py realwork/generate.py:generate.py realwork/verify.py:verify.py; do
    "$SSH" "$HOST" "cat > /workspace/noet-gpu/checks/${pair#*:}" < "$ROOT/protocol/${pair%%:*}"
  done
  "$SSH" "$HOST" 'set -e; cd /workspace/noet-gpu; chmod 600 /workspace/noet-live/*.b64 /workspace/noet-live/shared-secret /workspace/noet-live/miner-credentials
    chmod -R a+rX checks
    pkill -TERM -f "^python3 checks/live[.]py" || true
    # Anchored: the start command of a live Pod mentions checks/live.py too.
    for i in $(seq 1 60); do pgrep -f "^python3 checks/live[.]py" >/dev/null || break; sleep 2; done
    mkdir -p output/live
    nohup python3 checks/live.py > output/live/live-stdout.log 2>&1 < /dev/null &
    sleep 15; tail -5 output/live/supervisor.jsonl'
  echo "NOET_VERIFIER_ENDPOINT=$VERIFIER" | fly secrets import --app "$APP"
  echo "GPU attached to $APP; rewards go to the ecosystem vault $REWARD."
  ;;
web)
  check >/dev/null
  grep -q 'network: "noet-mainnet-1",[[:space:]]*launched: true' <(tr -s ' \n' ' ' < "$ROOT/web/src/pages/mine/config.ts") \
    || echo "Note: launched is still false for mainnet in web/src/pages/mine/config.ts; live mining figures stay hidden."
  cd "$ROOT/web"
  for pair in "NOET_EXPLORER_URL=https://$APP.fly.dev" "NOET_ADDRESS_HRP=noet" "VITE_NOET_NETWORK=main"; do
    name=${pair%%=*}
    vercel env rm "$name" production --yes >/dev/null 2>&1 || true
    printf '%s' "${pair#*=}" | vercel env add "$name" production >/dev/null
  done
  vercel env rm NOET_EXPLORER_TOKEN production --yes >/dev/null 2>&1 || true
  read_secret explorer-token | vercel env add NOET_EXPLORER_TOKEN production >/dev/null
  # Mainnet has no faucet.
  for name in NOET_FAUCET_URL NOET_FAUCET_TOKEN; do vercel env rm "$name" production --yes >/dev/null 2>&1 || true; done
  vercel pull --yes --environment=production >/dev/null
  set -a; eval "$(grep -E '^VITE_' .vercel/.env.production.local)"; set +a
  [[ "$VITE_NOET_NETWORK" == main ]] || { echo "Vercel did not switch to main"; exit 1; }
  npm run build >/dev/null
  vercel deploy --prebuilt --prod
  NOET_ORIGIN=https://www.noet.network node scripts/check-public.mjs
  ;;
*)
  echo "Unknown stage: $STAGE"; exit 2 ;;
esac
