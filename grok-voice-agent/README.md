# Grok Voice Agent - Church Website Assistant

A real-time voice assistant for your church website, powered by xAI's Grok Voice Agent API and LiveKit.

## Features

- 🎤 Real-time voice conversations with sub-second latency
- 🤖 Powered by Grok's speech-to-speech AI model
- 🔧 Custom tools for service times, events, contact info
- 🌐 Deploy to web, mobile, or telephony
- ☁️ Easy deployment to LiveKit Cloud

## Prerequisites

- Python 3.10 or higher
- LiveKit Cloud account (free tier available)
- xAI API key with credits

## Quick Start

### 1. Clone/Download this project

```bash
cd grok-voice-agent
```

### 2. Create a virtual environment (recommended)

**Using venv:**
```bash
python -m venv venv

# On Windows:
venv\Scripts\activate

# On macOS/Linux:
source venv/bin/activate
```

**Or using uv (faster):**
```bash
# Install uv if you don't have it
pip install uv

# Create and activate environment
uv venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
```

### 3. Install dependencies

**Using pip:**
```bash
pip install -r requirements.txt
```

**Or using uv:**
```bash
uv pip install -r requirements.txt
```

### 4. Set up environment variables

```bash
# Copy the example file
cp .env.example .env

# Edit .env with your actual credentials
# (use your favorite text editor)
```

Your `.env` file should contain:
```
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_api_key
LIVEKIT_API_SECRET=your_api_secret
XAI_API_KEY=your_xai_key
```

### 5. Run the agent locally

**Console Mode (talk in terminal):**
```bash
python agent.py console
```

**Dev Mode (connect to LiveKit Cloud):**
```bash
python agent.py dev
```

### 6. Test in the Playground

1. Go to [agents-playground.livekit.io](https://agents-playground.livekit.io)
2. Log in with your LiveKit credentials
3. Select your project
4. Click "Connect" and start talking!

## Customization

### Change the Voice

In `agent.py`, modify the `voice` parameter:

```python
llm=xai.realtime.RealtimeModel(
    voice="Ara",  # Options: Ara, Rex, Sal, Eve, Leo
)
```

| Voice | Description |
|-------|-------------|
| Ara | Warm, friendly female (default) |
| Rex | Confident, professional male |
| Sal | Smooth, balanced neutral |
| Eve | Energetic, upbeat female |
| Leo | Authoritative, strong male |

### Update Church Information

Edit the tool functions in `agent.py` to include your actual:
- Service times
- Church address
- Upcoming events
- Contact information
- Ministry descriptions

### Adjust Turn Detection

Fine-tune when the agent responds:

```python
turn_detection={
    "threshold": 0.5,           # Higher = needs louder speech
    "silence_duration_ms": 500,  # Lower = faster responses
}
```

## Deployment

### Deploy to LiveKit Cloud

1. Install LiveKit CLI:
```bash
# macOS
brew install livekit-cli

# Or download from: https://github.com/livekit/livekit-cli/releases
```

2. Authenticate:
```bash
lk cloud auth
```

3. Deploy:
```bash
lk app deploy
```

### Add to Your Website

You'll need a frontend to connect visitors to the agent. Options:

1. **LiveKit's React Components** - Easiest option
2. **Custom WebRTC implementation** - More control
3. **Embed the playground** - Quick testing

See LiveKit's frontend documentation for implementation details.

## Pricing

- **xAI Grok Voice**: $0.05/minute of conversation
- **LiveKit Cloud**: $0.01/minute (1,000 free minutes/month)
- **Total**: ~$0.06/minute when active

## Troubleshooting

### "Module not found" errors
Make sure you activated your virtual environment and installed dependencies.

### "Authentication failed"
Double-check your API keys in the `.env` file.

### Agent not responding
- Check that xAI has credits in your account
- Verify your microphone is working
- Try increasing `silence_duration_ms` if it's cutting off speech

### High latency
- Ensure stable internet connection
- Try a LiveKit region closer to you

## Resources

- [LiveKit Agents Documentation](https://docs.livekit.io/agents/)
- [xAI Grok Voice API Docs](https://docs.x.ai/docs/guides/voice)
- [LiveKit Community Slack](https://livekit.io/join-slack)

## License

MIT License - feel free to use and modify for your church!
