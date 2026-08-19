#!/usr/bin/env bash
# Fetch the HERB benchmark (Salesforce AI Research, EMNLP 2025 industry track).
#
# HERB is NOT redistributed in this repository. It is released by Salesforce for
# research purposes only, in support of their paper. This script downloads it
# directly from the original source so the licence stays with the publisher.
#
#   dataset : https://huggingface.co/datasets/Salesforce/HERB
#   code    : https://github.com/SalesforceAIResearch/HERB
#   paper   : Benchmarking Deep Search over Heterogeneous Enterprise Data
set -euo pipefail

# Use the project venv if present, else whatever python is on PATH.
PY=python
for c in .venv/Scripts/python.exe .venv/bin/python python3 python; do
  if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then PY="$c"; break; fi
done

BASE="https://huggingface.co/datasets/Salesforce/HERB/resolve/main"
API="https://huggingface.co/api/datasets/Salesforce/HERB"
OUT="data/herb"

mkdir -p "$OUT/products" "$OUT/metadata"
echo "listing files ..."
curl -sL -m 60 "$API" \
  | "$PY" -c "import json,sys;[print(f['rfilename']) for f in json.load(sys.stdin)['siblings'] if f['rfilename'].startswith(('products/','metadata/'))]" \
  | tr -d '\r' > /tmp/herb_files.txt

n=$(wc -l < /tmp/herb_files.txt)
echo "downloading $n files ..."
i=0
while read -r f; do
  [ -z "$f" ] && continue
  i=$((i+1))
  dest="$OUT/$f"
  mkdir -p "$(dirname "$dest")"
  if [ -s "$dest" ]; then echo "  [$i/$n] cached  $f"; continue; fi
  curl -sfL -m 180 "$BASE/$f" -o "$dest" && echo "  [$i/$n] ok      $f"
done < /tmp/herb_files.txt

echo
echo "done: $(ls "$OUT/products" | wc -l) products, $(ls "$OUT/metadata" | wc -l) metadata files"
du -sh "$OUT"
