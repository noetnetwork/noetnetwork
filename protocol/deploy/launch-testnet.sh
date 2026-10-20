#!/usr/bin/env bash
# Launch the hosted noet-testnet-1 node, attach the GPU verifier/miner, and
# switch the website. Each stage is separate and safe to re-run.
#
#   protocol/deploy/launch-testnet.sh node SECRETS_DIR
#   protocol/deploy/launch-testnet.sh gpu  SECRETS_DIR SSH_WRAPPER USER@HOST GPU_IP:PORT
#   protocol/deploy/launch-testnet.sh web  SECRETS_DIR
#
# SECRETS_DIR comes from protocol/deploy/network_secrets.py. Secret values are
# passed on stdin, never on the command line.
set -euo pipefail
STAGE=${1:?stage: node, gpu or web}
SECRETS=$(cd "${2:?secrets directory}" && pwd)
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
APP=noet-testnet
REGION=yyz
read_secret() { tr -d '\n' < "$SECRETS/$1"; }

case "$STAGE" in
node)
  fly apps list --json | grep -q "\"Name\": *\"$APP\"" || fly apps create "$APP" --org personal
  # Raw TCP services (relays, P2P) need a dedicated IPv4 address.
  fly ips list --app "$APP" --json | grep -q '"Type": *"v4"' || fly ips allocate-v4 --app "$APP" --yes
  fly ips list --app "$APP" --json | grep -q '"Type": *"v6"' || fly ips allocate-v6 --app "$APP"
  fly volumes list --app "$APP" --json | grep -q noet_testnet_data \
    || fly volumes create noet_testnet_data --app "$APP" --region "$REGION" --size 10 --yes
  {
    echo "NOET_EXPLORER_TOKEN=$(read_secret explorer-token)"
    echo "VALIDATOR_SHARED_SECRET=$(read_secret shared-secret)"
    echo "NOET_TLS_BUNDLE=$(read_secret certs/node-bundle.b64)"
    echo "NOET_MINER_RPCAUTH=$(read_secret miner-rpcauth)"
    echo "NOET_FAUCET_TOKEN=$(read_secret faucet-token)"
    echo "NOET_FAUCET_PASSPHRASE=$(read_secret faucet-passphrase)"
  } | fly secrets import --app "$APP" --stage
  CTX=$(mktemp -d)/context
  python3 "$ROOT/protocol/deploy/fly/prepare.py" --out "$CTX" --profile testnet >/dev/null
  (cd "$CTX" && fly deploy --remote-only --app "$APP" --strategy immediate)
  fly ips list --app "$APP"
  echo "Node deployed. Genesis check:"
  curl -fsS "https://$APP.fly.dev:9443/mining/info"; echo
  ;;
gpu)
  SSH=${3:?ssh wrapper}
  HOST=${4:?user@host}
  VERIFIER=${5:?verifier endpoint ip:port as reachable from Fly}
  NODE_IP=$(fly ips list --app "$APP" --json | python3 -c "import json,sys;print([i['Address'] for i in json.load(sys.stdin) if i['Type']=='v4'][0])")
  REWARD=$(fly ssh console --app "$APP" -C "cat /data/faucet-address" | tr -d '\r\n ')
  [[ "$REWARD" == tnoet1* ]] || { echo "Faucet address not ready yet: $REWARD"; exit 1; }
  # The GPU host serves one network at a time: replace its live configuration.
  "$SSH" "$HOST" 'set -e; L=/workspace/noet-live; mkdir -p $L/previous; cp -p $L/live.json $L/gpu-bundle.b64 $L/shared-secret $L/miner-credentials $L/previous/'
  "$SSH" "$HOST" 'cat > /workspace/noet-live/gpu-bundle.b64' < "$SECRETS/certs/gpu-bundle.b64"
  "$SSH" "$HOST" 'cat > /workspace/noet-live/shared-secret' < "$SECRETS/shared-secret"
  "$SSH" "$HOST" 'cat > /workspace/noet-live/miner-credentials' < "$SECRETS/miner-credentials"
  printf '{"node": "%s", "verdictPort": 17443, "rpcPort": 18443, "rewardAddress": "%s", "network": "testnet", "gateway": "https://%s.fly.dev:9443"}\n' \
    "$NODE_IP" "$REWARD" "$APP" | "$SSH" "$HOST" 'cat > /workspace/noet-live/live.json'
  "$SSH" "$HOST" 'cat > /workspace/noet-gpu/checks/noet_miner.py' < "$ROOT/protocol/miner/noet_miner.py"
  "$SSH" "$HOST" 'cat > /workspace/noet-gpu/checks/live.py' < "$ROOT/protocol/gpu/live.py"
  "$SSH" "$HOST" 'cat > /workspace/noet-gpu/checks/generate.py' < "$ROOT/protocol/realwork/generate.py"
  # The verifier receiver too: without it, a fix to realwork/verify.py never reaches the GPU
  # (the 1,000-height bound stopped the testnet on 14 October).
  "$SSH" "$HOST" 'cat > /workspace/noet-gpu/checks/verify.py' < "$ROOT/protocol/realwork/verify.py"
  "$SSH" "$HOST" 'set -e; cd /workspace/noet-gpu; chmod 600 /workspace/noet-live/*.b64 /workspace/noet-live/shared-secret /workspace/noet-live/miner-credentials
    pkill -TERM -f "^python3 checks/live[.]py" || true
    for i in $(seq 1 60); do pgrep -f "checks/live[.]py" >/dev/null || break; sleep 2; done
    nohup python3 checks/live.py > output/live/live-stdout.log 2>&1 < /dev/null &
    sleep 15; tail -5 output/live/supervisor.jsonl'
  echo "NOET_VERIFIER_ENDPOINT=$VERIFIER" | fly secrets import --app "$APP"
  echo "GPU attached to $APP; reward address $REWARD."
  ;;
web)
  cd "$ROOT/web"
  for pair in "NOET_EXPLORER_URL=https://$APP.fly.dev" "NOET_FAUCET_URL=https://$APP.fly.dev:8443" \
              "NOET_ADDRESS_HRP=tnoet" "VITE_NOET_NETWORK=test"; do
    name=${pair%%=*}
    vercel env rm "$name" production --yes >/dev/null 2>&1 || true
    printf '%s' "${pair#*=}" | vercel env add "$name" production >/dev/null
  done
  for name in NOET_EXPLORER_TOKEN NOET_FAUCET_TOKEN; do
    file=$([[ $name == NOET_EXPLORER_TOKEN ]] && echo explorer-token || echo faucet-token)
    vercel env rm "$name" production --yes >/dev/null 2>&1 || true
    read_secret "$file" | vercel env add "$name" production >/dev/null
  done
  vercel deploy --prod
  NOET_ORIGIN=https://www.noet.network node scripts/check-public.mjs
  ;;
*)
  echo "Unknown stage: $STAGE"; exit 2 ;;
esac
