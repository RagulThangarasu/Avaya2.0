#!/bin/bash
cd /Users/ragul/Desktop/Avayaa-2.0
git add -A
git commit -m "Content validation optimization: Skip header/footer/nav, validate only h2/h3/p

- Extract only h2, h3, and paragraph content (skip headers, footers, nav)
- 10 concurrent batch validation (3-4x faster)
- 30s page timeout optimization
- Detailed diff reporting with word counts and sample terms
- 16+ columns in Excel report with mismatch analysis"
git push -u origin main
