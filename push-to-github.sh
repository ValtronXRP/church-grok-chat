#!/bin/bash

echo "Pushing to ValtronXRP/church-grok-chat..."

# Add all files
git add .

# Commit
git commit -m "Church Chat with sermon search and YouTube embeds"

# Add ValtronXRP's GitHub repo
git remote add origin https://github.com/ValtronXRP/church-grok-chat.git

# Push to GitHub
git branch -M main
git push -u origin main

echo "✅ Code pushed to https://github.com/ValtronXRP/church-grok-chat"
