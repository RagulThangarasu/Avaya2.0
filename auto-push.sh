#!/bin/bash

# Auto-push all changes to GitHub
cd /Users/ragul/Desktop/Avayaa-2.0

echo "📦 Staging changes..."
git add -A

echo "📝 Committing changes..."
git commit -m "Hide AEM status badge and update README for local server setup

- Hide AEM Connected/Disconnected badge in header
- Set display: none on .aem-status-badge CSS
- Updated README with server setup instructions
- Added web UI features and API documentation
- Added example workflow for running validations locally"

echo "🚀 Pushing to GitHub..."
git push origin main

if [ $? -eq 0 ]; then
  echo "✅ Successfully pushed to GitHub!"
  git log -1 --oneline
else
  echo "❌ Push failed. Please check your network connection and credentials."
  exit 1
fi
