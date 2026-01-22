#!/bin/bash

# After creating repo on GitHub, run these commands:
# Replace YOUR_GITHUB_USERNAME with your actual GitHub username

echo "Setting up GitHub repository..."

# Add your files
git add .

# Create first commit
git commit -m "Initial commit - Church Chat with YouTube embeds and sermon search"

# Add GitHub remote for ValtronXRP
git remote add origin https://github.com/ValtronXRP/church-grok-chat.git

# Rename branch to main
git branch -M main

# Push to GitHub
git push -u origin main

echo "Done! Your code is now on GitHub"