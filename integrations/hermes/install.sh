#!/usr/bin/env bash
# Install the Seiche desk-agent kit into a Hermes home.
#
#   ./install.sh                 # installs into ~/.hermes
#   ./install.sh --hermes-home /path/to/home
#
# Idempotent: re-running refreshes the seiche-* skills in place. It never
# touches your config.yaml, .env, or an existing AGENTS.md; it prints the
# manual steps for those instead.

set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HOME}/.hermes"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hermes-home) HERMES_HOME="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ ! -d "$HERMES_HOME" ]]; then
  echo "error: $HERMES_HOME does not exist. Install hermes-agent first:" >&2
  echo "  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash" >&2
  exit 1
fi

SKILLS_DEST="$HERMES_HOME/skills/seiche"
mkdir -p "$SKILLS_DEST"
installed=()
for skill in "$KIT_DIR"/skills/*/; do
  name="$(basename "$skill")"
  rm -rf "${SKILLS_DEST:?}/$name"
  cp -R "$skill" "$SKILLS_DEST/$name"
  installed+=("$name")
done
echo "Installed ${#installed[@]} skills into $SKILLS_DEST:"
printf '  - %s\n' "${installed[@]}"

echo
if [[ -f "$HERMES_HOME/AGENTS.md" ]]; then
  echo "NOTE: $HERMES_HOME/AGENTS.md already exists; not touching it."
  echo "      Merge the persona from $KIT_DIR/AGENTS.md yourself."
else
  cp "$KIT_DIR/AGENTS.md" "$HERMES_HOME/AGENTS.md"
  echo "Installed the desk-agent persona as $HERMES_HOME/AGENTS.md"
fi

cat <<EOF

Next steps (manual, one time):
  1. Wire the Seiche MCP server and platform toolsets into
     $HERMES_HOME/config.yaml
     using the fragments in: $KIT_DIR/config.example.yaml
  2. Add secrets to $HERMES_HOME/.env  (see $KIT_DIR/env.example)
  3. Start the gateway:  hermes gateway
  4. Send the bootstrap message from: $KIT_DIR/BOOTSTRAP.md

Docs: docs/HERMES.md in the seiche repo.
EOF
