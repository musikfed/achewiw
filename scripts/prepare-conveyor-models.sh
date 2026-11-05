#!/usr/bin/env bash
# Explicit host-only model preparation for a 12 GiB RTX 3060.
# Does NOT modify any other Ollama model, daemon, Docker container or GPU.
set -Eeuo pipefail
MODE="${1:-status}"
WRITER="qwen3.5:9b-q4_K_M"
EDITOR="gemma3:12b"
case "$MODE" in
  status|pull-writer|pull-editor|pull-both) ;;
  *) echo "Usage: bash scripts/prepare-conveyor-models.sh [status|pull-writer|pull-editor|pull-both]" >&2; exit 2 ;;
esac
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama is not on PATH; this must run on Ubuntu CORE host." >&2
  exit 1
fi
echo "Ollama: $(ollama --version)"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader
else
  echo "NVIDIA driver cannot be checked here; no installation or restart attempted." >&2
fi
echo "Current models:"
ollama list
case "$MODE" in
 status) exit 0 ;;
 pull-writer) ollama pull "$WRITER" ;;
 pull-editor) ollama pull "$EDITOR" ;;
 pull-both) ollama pull "$WRITER"; ollama pull "$EDITOR" ;;
esac
echo "Downloads complete; nothing was loaded into VRAM or deleted."
ollama list
