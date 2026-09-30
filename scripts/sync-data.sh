#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "$0")/../../cryptd" && pwd)"
cd "$repo"
nix develop . --command ./target/release/cryptd data sync --source tdx --workspace /home/fikgol/data/tdx/cryptd-workspace --to 2026-09-30 --json
nix develop . --command ./target/release/cryptd data build --source tdx --workspace /home/fikgol/data/tdx/cryptd-workspace --json
