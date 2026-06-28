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

FILES=$(find Source -type f \
  ! -path "*/__pycache__/*" \
  ! -name ".DS_Store" \
  ! -path "Source/state/*" | wc -l)

REMOTE_FILES=$(ssh "$PI" "find $REMOTE -type f \
  ! -path '$REMOTE/state/*' \
  ! -path '$REMOTE/Recoveries/*' | wc -l")

if [ "$FILES" -eq "$REMOTE_FILES" ]; then
    echo "Verification passed."
else
    echo "Verification FAILED."
    echo "Local files : $FILES"
    echo "Remote files: $REMOTE_FILES"
    exit 1
fi

echo
echo "SUCCESS: Deployment complete."
echo "========================================="