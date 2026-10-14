#!/usr/bin/env bash
# Generates fresh per-deployment secrets and flags for the "Second
# Opinion" room. Run this once before `docker compose up --build`.
#
# Writes:
#   .env           -- ORCH_API_KEY, METADATA_NONCE, USER_FLAG
#                      (consumed by docker-compose.yml; never commit this)
#   /root/root.txt -- root flag, written directly to THIS host's
#                      filesystem (requires sudo), because the room's
#                      final step is a Docker-socket escape OUT of a
#                      container onto the host that's running Docker.
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  echo ".env already exists -- refusing to overwrite." >&2
  echo "Delete it first if you really want to rotate secrets." >&2
  exit 1
fi

rand_flag() {
  # 20 chars, guaranteed lower + upper + digit + special, then shuffled.
  # Special set deliberately excludes $ \ " ' { } ` & | ; < > ( ) [ ] --
  # anything that would need escaping in shell, JSON, or inside THM{...}
  # itself.
  local lower upper digit special all pick body
  lower="abcdefghijklmnopqrstuvwxyz"
  upper="ABCDEFGHIJKLMNOPQRSTUVWXYZ"
  digit="0123456789"
  special="!@#%^*-_=+."
  all="${lower}${upper}${digit}${special}"

  pick() { local s="$1"; printf '%s' "${s:$((RANDOM % ${#s})):1}"; }

  body="$(pick "$lower")$(pick "$upper")$(pick "$digit")$(pick "$special")"
  for _ in $(seq 1 16); do
    body+="$(pick "$all")"
  done
  body="$(printf '%s' "$body" | fold -w1 | shuf | tr -d '\n')"
  printf 'THM{%s}' "$body"
}

ORCH_API_KEY="$(openssl rand -hex 16)"
METADATA_NONCE="$(( (RANDOM % 254) + 1 ))"   # 1-254, single-byte XOR key
USER_FLAG="$(rand_flag)"
ROOT_FLAG="$(rand_flag)"

cat > .env <<EOF
ORCH_API_KEY=${ORCH_API_KEY}
METADATA_NONCE=${METADATA_NONCE}
USER_FLAG=${USER_FLAG}
EOF
chmod 600 .env

echo "[+] Wrote .env (ORCH_API_KEY, METADATA_NONCE, USER_FLAG) -- not committed, see .gitignore"
echo "[+] Root flag: ${ROOT_FLAG}"
echo

read -r -p "Write this root flag to /root/root.txt on THIS host now (requires sudo)? [y/N] " ans
if [[ "${ans:-}" =~ ^[Yy]$ ]]; then
  sudo bash -c "printf '%s\n' '${ROOT_FLAG}' > /root/root.txt && chmod 600 /root/root.txt"
  echo "[+] Wrote /root/root.txt"
else
  echo "[!] Skipped. Before publishing the room, run:"
  echo "    sudo bash -c \"printf '%s\n' '${ROOT_FLAG}' > /root/root.txt && chmod 600 /root/root.txt\""
fi

echo
echo "Next: docker compose up --build"
