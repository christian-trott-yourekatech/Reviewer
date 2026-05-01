#!/usr/bin/env bash

set -euo pipefail

# Install SQA Agent
uv tool install --reinstall --with "sqa-agent[all]" git+https://${REVIEWER_REPO_ACCESS_TOKEN}@github.com/christian-trott-yourekatech/Reviewer.git
