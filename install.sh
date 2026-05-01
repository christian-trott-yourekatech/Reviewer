#!/usr/bin/env bash

set -euo pipefail

# Install (or upgrade) SQA Agent from the public repository.
# Re-running this script reinstalls the latest version.
uv tool install --reinstall \
    --with "sqa-agent[all]" \
    git+https://github.com/christian-trott-yourekatech/Reviewer.git
