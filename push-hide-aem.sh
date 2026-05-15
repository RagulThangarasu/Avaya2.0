#!/bin/bash
cd /Users/ragul/Desktop/Avayaa-2.0

# Create new branch
BRANCH_NAME="hide-aem-status-$(date +%s)"
git checkout -b "$BRANCH_NAME"

# Add all changes
git add -A

# Commit
git commit -m "Hide AEM Connected status badge in header - disable completely

- Set display: none on .aem-status-badge CSS class
- Disabled in broken-links.html
- Disabled in metadata-validation.html
- AEM status check remains in background but hidden from UI
- Cleaner header without connection indicator"

# Push to GitHub
git push -u origin "$BRANCH_NAME"

echo "✅ Branch created and pushed: $BRANCH_NAME"
echo "🔗 Visit: https://github.com/[repo]/tree/$BRANCH_NAME"
