#!/bin/bash

set -e

PI="MiniBerry@192.168.1.36"
REMOTE="/home/MiniBerry/drs"
BACKUP="/home/MiniBerry/drs-backup"

echo "========================================="
echo " Project Sentinel Deployment"
echo "========================================="

echo "Backing up current runtime..."
ssh "$PI" "rm -rf $BACKUP && cp -a $REMOTE $BACKUP 2>/dev/null || true"

echo "Deploying Source..."
ssh "$PI" "mkdir -p $REMOTE"

rsync -av \
  --exclude='__pycache__' \
  --exclude='.DS_Store' \
  --exclude='state/' \
  --exclude='Recoveries/' \
  Source/ "$PI:$REMOTE/"

echo "Verifying deployment..."

VERIFY_OUTPUT=$(rsync -rcn --itemize-changes \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  --exclude='state/' \
  --exclude='Recoveries/' \
  Source/ "$PI:$REMOTE/" 2>&1) || {
    echo "Verification FAILED."
    printf '%s\n' "$VERIFY_OUTPUT"
    exit 1
}

DIFFS=$(printf '%s\n' "$VERIFY_OUTPUT" | grep -E '^>' || true)

if [ -n "$DIFFS" ]; then
    echo "Verification FAILED."
    printf '%s\n' "$DIFFS"
    exit 1
fi

echo "Verification passed."

echo
echo "SUCCESS: Deployment complete."
echo "========================================="