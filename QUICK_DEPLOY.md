# Quick Deploy to Railway in 5 Minutes! 🚀

## Step 1: Push to GitHub

```bash
cd /Users/valorkopeny/Desktop/church-grok-chat
git init
git add .
git commit -m "Initial commit - Church Chat App"
gh repo create church-grok-chat --public --push
```

## Step 2: Deploy to Railway

1. **Go to**: https://railway.app
2. **Sign in with GitHub**
3. **Click**: "New Project" → "Deploy from GitHub repo"
4. **Select**: `church-grok-chat`
5. **Wait**: ~2 minutes for deployment

## Step 3: Add Environment Variables

In Railway dashboard, click your project, then "Variables" tab, add:

```
LIVEKIT_API_KEY=your_livekit_key_here
LIVEKIT_API_SECRET=your_livekit_secret_here
LIVEKIT_URL=wss://your-app.livekit.cloud
XAI_API_KEY=your_xai_key_here
```

## Step 4: Get Your Public URL

Your app is now live at:
```
https://your-app-name.up.railway.app/chat.html
```

## That's it! 🎉

YouTube videos will now embed properly since you're on HTTPS!

---

## Alternative: One-Click Deploy with Vercel

1. Push to GitHub (Step 1 above)
2. Go to: https://vercel.com/new
3. Import your GitHub repo
4. Add environment variables
5. Deploy!

Your app will be at: `https://church-chat.vercel.app`