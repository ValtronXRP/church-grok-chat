#!/bin/bash

# Use SSH instead of HTTPS
git remote add origin git@github.com:ValtronXRP/church-grok-chat.git

# Push to GitHub
git push -u origin main

echo "✅ Successfully pushed via SSH!"
