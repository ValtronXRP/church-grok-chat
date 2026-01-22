# Deployment Instructions for Church Chat App

## Quick Deploy Options

### Option 1: Deploy to Railway (Recommended - Easiest)

1. **Sign up for Railway**: https://railway.app
2. **Install Railway CLI** (optional):
   ```bash
   npm install -g @railway/cli
   ```

3. **Deploy via GitHub** (Recommended):
   - Push your code to GitHub
   - Go to Railway dashboard
   - Click "New Project" → "Deploy from GitHub"
   - Select your repository
   - Railway will auto-detect Node.js app

4. **Add Environment Variables in Railway**:
   - Click on your project
   - Go to "Variables" tab
   - Add these variables:
   ```
   LIVEKIT_API_KEY=your_key
   LIVEKIT_API_SECRET=your_secret
   LIVEKIT_URL=wss://your-server.livekit.cloud
   XAI_API_KEY=your_xai_key
   PORT=3001
   ```

5. **Your app will be live at**: `https://your-app-name.up.railway.app`

### Option 2: Deploy to Render

1. **Sign up**: https://render.com
2. **Create New Web Service**
3. **Connect GitHub repo**
4. **Configure**:
   - Environment: Node
   - Build Command: `npm install`
   - Start Command: `npm start`
5. **Add Environment Variables** (same as above)
6. **Deploy!**

### Option 3: Deploy to Heroku

1. **Install Heroku CLI**: https://devcenter.heroku.com/articles/heroku-cli

2. **Login and Create App**:
   ```bash
   heroku login
   heroku create your-app-name
   ```

3. **Set Environment Variables**:
   ```bash
   heroku config:set LIVEKIT_API_KEY=your_key
   heroku config:set LIVEKIT_API_SECRET=your_secret
   heroku config:set LIVEKIT_URL=wss://your-server.livekit.cloud
   heroku config:set XAI_API_KEY=your_xai_key
   ```

4. **Deploy**:
   ```bash
   git add .
   git commit -m "Deploy to Heroku"
   git push heroku main
   ```

5. **Open Your App**:
   ```bash
   heroku open
   ```

## Important: Sermon API Deployment

The sermon search API (`sermon_api.py`) needs to be deployed separately as a Python service.

### Deploy Sermon API to Railway:

1. **Create separate Railway project** for Python API
2. **Add `requirements.txt`** (already exists as `requirements_sermon.txt`)
3. **Add `runtime.txt`**:
   ```
   python-3.9.18
   ```
4. **Update `sermon_api.py`** to use PORT from environment:
   ```python
   port = int(os.environ.get('PORT', 5001))
   app.run(host='0.0.0.0', port=port)
   ```
5. **Update `server.js`** to use production sermon API URL:
   ```javascript
   const SERMON_API_URL = process.env.SERMON_API_URL || 'http://localhost:5001';
   // Then use SERMON_API_URL in axios calls
   ```

## Environment Variables Needed

### For Main App (Node.js):
- `LIVEKIT_API_KEY` - From LiveKit Cloud
- `LIVEKIT_API_SECRET` - From LiveKit Cloud
- `LIVEKIT_URL` - Your LiveKit server URL
- `XAI_API_KEY` - From x.ai console
- `SERMON_API_URL` - URL of deployed sermon API
- `PORT` - Usually set automatically by hosting service

### For Sermon API (Python):
- `PORT` - Usually set automatically

## Post-Deployment Checklist

1. ✅ Test chat at: `https://your-app.railway.app/chat.html`
2. ✅ Verify YouTube videos embed properly (should work on HTTPS)
3. ✅ Test sermon search returns results
4. ✅ Test voice agent connection
5. ✅ Check all environment variables are set

## Troubleshooting

### YouTube Videos Not Loading:
- Should work fine on HTTPS (public deployment)
- Check browser console for CSP errors

### Sermon Search Not Working:
- Verify sermon API is deployed and running
- Check `SERMON_API_URL` environment variable
- Test API directly: `https://your-sermon-api.railway.app/api/sermon/health`

### Voice Agent Not Connecting:
- Check LiveKit credentials are correct
- Verify LiveKit server is running
- Check agent_smart.py is deployed and running

## Recommended Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│                 │────▶│                  │────▶│                 │
│  Frontend       │     │  Node.js Server  │     │  Sermon API     │
│  (chat.html)    │     │  (Railway #1)    │     │  (Railway #2)   │
│                 │◀────│                  │◀────│                 │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │                           │
                               ▼                           ▼
                        ┌──────────────┐          ┌──────────────┐
                        │   LiveKit    │          │  Vector DB   │
                        │    Cloud     │          │  (ChromaDB)  │
                        └──────────────┘          └──────────────┘
```

## Quick Start Commands

```bash
# Railway deployment (easiest)
railway login
railway link
railway up

# Your app will be live in ~2 minutes!
```

## Support

If you need help with deployment:
1. Check Railway docs: https://docs.railway.app
2. Check Render docs: https://render.com/docs
3. Check Heroku docs: https://devcenter.heroku.com