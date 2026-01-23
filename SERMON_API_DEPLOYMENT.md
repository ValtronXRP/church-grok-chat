# Sermon API Deployment Options

Your sermon database is 423MB, which is too large for simple deployment. Here are your options:

## Option 1: Use ngrok (Quick Solution)
1. Install ngrok: `brew install ngrok`
2. Run: `ngrok http 5001`
3. Copy the public URL (e.g., `https://abc123.ngrok.io`)
4. Update Railway environment variable:
   - Go to Railway dashboard
   - Add/Update: `SERMON_API_URL=https://abc123.ngrok.io`
   - Railway will redeploy automatically

## Option 2: Deploy Sermon API Separately
Since the database is large, you could:
1. Use a cloud database service (e.g., Supabase, PostgreSQL)
2. Deploy the sermon API to a separate service
3. Update the SERMON_API_URL in Railway

## Option 3: Use Static JSON (Simplest for Testing)
For now, let's update your server to work without the sermon API:

The sermons won't show up, but at least the chat will work.