#!/usr/bin/env bash
# Manual check of advancement announcements on the real server.
# Usage: scripts/adv-test.sh <nick>
#   the player must be online and not in ALLAY_HIDDEN.
# Revokes the tested advancements first, grants them step by step, and revokes them
# again at the end (also on Ctrl+C), then restarts the bot so it forgets what it announced.
# The player loses these advancements even if they had them before: use a test account.
set -euo pipefail

NICK="${1:?usage: adv-test.sh <nick>}"
MC="${MC_CONTAINER:-mc}"
BOT="${BOT_CONTAINER:-allay}"
WAIT=50  # > ADVANCEMENT_BATCH_SECONDS in allay/bot.py

NOISE=(story/mine_stone)            # task outside NOTABLE_TASKS: must be silent
SINGLE=(story/mine_diamond)         # one message with description
BATCH=(                             # one message, a line per advancement
  story/shiny_gear
  story/enter_the_nether
  story/enter_the_end
  nether/find_fortress
  nether/obtain_ancient_debris
  nether/get_wither_skull
  nether/summon_wither
  end/kill_dragon                   # goal with its own icon
  end/elytra                        # goal with its own icon
  adventure/totem_of_undying        # goal without icon: frame icon
  adventure/kill_all_mobs           # challenge without icon: frame icon
)
ALL=("${NOISE[@]}" "${SINGLE[@]}" "${BATCH[@]}")

rcon() { docker exec "$MC" rcon-cli "$@"; }

grant() { for a in "$@"; do rcon advancement grant "$NICK" only "minecraft:$a"; done; }

revoke_all() {
  echo "== revoking tested advancements from $NICK"
  for a in "${ALL[@]}"; do rcon advancement revoke "$NICK" only "minecraft:$a" || true; done
}

step() {
  echo
  echo "== $1"
  echo "   expected: $2"
}

wait_batch() {
  echo "   waiting ${WAIT}s for the batch to flush..."
  sleep "$WAIT"
  read -rp "   check the chat, Enter to continue "
}

cleanup() {
  echo
  revoke_all
  echo "== restarting $BOT (it remembers announced advancements in memory)"
  docker restart "$BOT" >/dev/null
  echo "done"
}

revoke_all
trap cleanup EXIT

step "noise: ${NOISE[*]}" "nothing in the chat, no 'advancement:' line in the bot log"
grant "${NOISE[@]}"
wait_batch

step "single: ${SINGLE[*]}" "one message with icon and description; silent unless first on the server"
grant "${SINGLE[@]}"
wait_batch

step "batch: ${#BATCH[@]} advancements" "one message 'получил достижения:' with a line and icon per advancement"
grant "${BATCH[@]}"
wait_batch
