#!/bin/bash

# Create and push new branch with all changes
cd /Users/ragul/Desktop/Avayaa-2.0

# Create a new branch with timestamp
BRANCH_NAME="content-validation-optimization-$(date +%s)"

# Git operations
git checkout -b "$BRANCH_NAME"
git add -A
git commit -m "Content validation optimization: Skip header/footer/nav - validate only h2, h3, p

- Modified get_page_text() to extract only h2, h3, and paragraph content
- Skip elements in header, footer, nav tags and related CSS classes
- Improved accuracy by ignoring navigation-related content
- Added DOM structure inspection script (inspect_dom.py) for debugging page layouts
- Increased batch size to 10 concurrent validations (3-4x faster)
- Reduced page load timeout from 60s to 30s
- Enhanced diff reporting with word count and term samples
- Excel report now includes 16+ columns with detailed mismatch analysis"

# Push to remote
git push -u origin "$BRANCH_NAME"

echo "✅ Branch created and pushed: $BRANCH_NAME"
echo "📊 You can view changes at: git log --oneline -10"
