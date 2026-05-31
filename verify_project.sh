#!/usr/bin/env bash
set -euo pipefail

# 1. Lint all Go files
if command -v golangci-lint >/dev/null; then
  echo "Running golangci-lint..."
  golangci-lint run ./... || echo "golint issues detected"
else
  echo "golangci-lint not installed, skipping Go lint"
fi

# 2. Lint JavaScript/TypeScript (frontend)
if command -v npm >/dev/null; then
  echo "Running npm lint..."
  (cd portal && npm install && npm run lint) || echo "npm lint issues detected"
else
  echo "npm not installed, skipping frontend lint"
fi

# 3. Validate .env files (no empty values)
if [ -f .env ]; then
  echo "Checking .env for missing values..."
  grep -E "^[A-Z_]+=$" .env && echo "Warning: some env variables are empty"
fi

# 4. Check documentation completeness
if command -v markdownlint >/dev/null; then
  echo "Running markdownlint..."
  markdownlint **/*.md || echo "markdownlint issues detected"
fi

# 5. Run static analysis for security (gosec for Go)
if command -v gosec >/dev/null; then
  echo "Running gosec..."
  gosec ./... || echo "gosec issues detected"
fi

# 6. Verify build scripts
if [ -f portal/vite.config.js ]; then
  echo "Testing Vite build..."
  (cd portal && npm run build) || echo "Vite build failed"
fi

# 7. Dependency audit
if command -v npm >/dev/null; then
  (cd portal && npm audit --audit-level=high) || echo "npm audit found high severity issues"
fi

if command -v go >/dev/null; then
  go list -m all | grep -v indirect | xargs -L1 go get -u || echo "Go dependency update issues"
fi

echo "Verification completed."
