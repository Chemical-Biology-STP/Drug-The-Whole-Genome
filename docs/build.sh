#!/bin/bash
# Rebuild the MkDocs documentation site.
# Run from the project root: bash docs/build.sh
set -euo pipefail
pixi run mkdocs build -f docs/mkdocs.yml
echo "Docs built → webapp/static/docs/"
