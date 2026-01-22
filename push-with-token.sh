#!/bin/bash

echo "Enter your GitHub Personal Access Token for ValtronXRP:"
read -s GITHUB_TOKEN

# Add remote with token authentication
git remote add origin https://ValtronXRP:${GITHUB_TOKEN}@github.com/ValtronXRP/church-grok-chat.git

# Push to GitHub
git push -u origin main

echo "✅ Successfully pushed to ValtronXRP/church-grok-chat!"
