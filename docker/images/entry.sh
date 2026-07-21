#!/bin/bash
# DDP pod entrypoint.
#   POD_MODE=ssh  -> debug pod: sshd in foreground
#   POD_MODE=run  -> gpu job pod: sshd in background + run ENTRY_COMMAND in /workspace
set -e

# children (sshd, entry command) must not re-source the NGC shinit checks
unset BASH_ENV

echo "root:${SSH_PASSWORD:-ddp123}" | chpasswd
mkdir -p /run/sshd /workspace
ln -sfn /workspace /root/workspace
cd /workspace

if [ "$POD_MODE" = "run" ]; then
  /usr/sbin/sshd -e
  echo "=== ${ENTRY_COMMAND:-bash} ==="
  # tee into the workspace pvc so logs survive pod deletion (deadline kills)
  mkdir -p /workspace/.ddp-logs
  bash -lc "${ENTRY_COMMAND:-bash}" 2>&1 | tee "/workspace/.ddp-logs/${JOB_ID:-unknown}.log"
  exit "${PIPESTATUS[0]}"
else
  exec /usr/sbin/sshd -D -e
fi
