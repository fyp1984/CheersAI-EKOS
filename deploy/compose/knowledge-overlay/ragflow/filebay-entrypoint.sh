#!/usr/bin/env bash
set -euo pipefail

# Keep RAGFlow itself unmodified. The official image serves a prebuilt SPA;
# this deployment-only wrapper adds a small FileBay navigation shell at start.
index_file=/ragflow/web/dist/index.html
adapter_version=20260912
if [[ -f "${index_file}" ]]; then
	# Idempotent injection: strip every previously injected adapter tag first
	# (volume reuse across service recreations can leave duplicates behind),
	# then inject exactly one pair with the current cache-busting version.
	sed -i 's#<link rel="stylesheet" href="/ragflow/filebay-shell\.css[^"]*">##g; s#<script src="/ragflow/filebay-shell\.js[^"]*"></script>##g' "${index_file}"
	sed -i 's#</head>#<link rel="stylesheet" href="/ragflow/filebay-shell.css?v='"${adapter_version}"'"><script src="/ragflow/filebay-shell.js?v='"${adapter_version}"'"></script></head>#' "${index_file}"
fi

exec /ragflow/entrypoint.sh "$@"
