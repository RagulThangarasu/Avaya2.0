#!/bin/bash

# Heroku Deployment Helper Script
# Usage: ./heroku-deploy.sh <your-app-name>

APP_NAME=$1

if [ -z "$APP_NAME" ]; then
  echo "❌ Error: Please provide a Heroku app name."
  echo "Usage: ./heroku-deploy.sh my-avaya-validator"
  exit 1
fi

echo "🚀 Starting Heroku Deployment for: $APP_NAME"

# 1. Add Heroku Remote
heroku git:remote -a $APP_NAME

# 2. Set Buildpacks (Order is important: Python first, then Node, then Playwright)
echo "📦 Setting up buildpacks..."
heroku buildpacks:clear
heroku buildpacks:add heroku/python
heroku buildpacks:add heroku/nodejs
heroku buildpacks:add https://github.com/jontewks/puppeteer-heroku-buildpack

# 3. Commit deployment files
echo "📝 Committing deployment files..."
git add Procfile requirements.txt package.json
git commit -m "Add Heroku deployment configuration"

# 4. Push to Heroku
echo "🚀 Pushing to Heroku..."
git push heroku feature/enhanced-structure-report:main

echo "✅ Done! Your app should be live at: https://$APP_NAME.herokuapp.com"
