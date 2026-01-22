# Deployment Guide for Church Grok Chat

## Repository
Your code is now available at: https://github.com/ValtronXRP/church-grok-chat

## Railway Deployment (Recommended)

### Step 1: Sign up for Railway
1. Go to https://railway.app
2. Sign in with your GitHub account (ValtronXRP)

### Step 2: Create New Project
1. Click "New Project"
2. Choose "Deploy from GitHub repo"
3. Select "ValtronXRP/church-grok-chat"

### Step 3: Configure Environment Variables
Add these environment variables in Railway:

```
XAI_API_KEY=<your-xai-api-key>
LIVEKIT_URL=<your-livekit-url>
LIVEKIT_API_KEY=<your-livekit-api-key>
LIVEKIT_API_SECRET=<your-livekit-api-secret>
SERMON_API_URL=http://localhost:5001
PORT=3001
```

### Step 4: Deploy
1. Railway will automatically deploy your app
2. You'll get a public URL like: `church-grok-chat.railway.app`
3. Access your chat at: `https://church-grok-chat.railway.app/chat.html`

## Important Notes

### YouTube Embedding
- YouTube videos will now work properly on the public HTTPS URL
- The Content-Security-Policy headers are already configured in server.js

### Voice Agent
- The voice agent (agent_smart.py) needs to run separately
- Consider using a separate Railway service or a VPS for the Python agent

### Sermon API
- The sermon_api.py server needs to run alongside the main server
- You may need to deploy it as a separate service or combine it with server.js

## Alternative: Vercel Deployment

If Railway doesn't work, you can try Vercel:

1. Install Vercel CLI: `npm i -g vercel`
2. Run: `vercel` in the project directory
3. Follow the prompts
4. Add environment variables in Vercel dashboard

## Testing the Deployment

Once deployed, test these features:
1. Text chat with Grok
2. YouTube video embedding in responses
3. Sermon search functionality
4. Voice agent connection (if deployed)

## Monitoring

Check the logs in Railway/Vercel dashboard for any errors.

## Support

If you encounter issues:
1. Check that all environment variables are set
2. Verify the sermon database is accessible
3. Ensure LiveKit credentials are correct