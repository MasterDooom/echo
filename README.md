# Echo

A lightweight, local Windows voice assistant for Spotify. Say **"Echo next"**, **"Echo pause"**, or **"Echo play Save Your Tears"**. Echo stays in the background and shows a small status pill above the taskbar.

## Stack

- Vosk: offline wake detection and short-command speech recognition
- SoundDevice: microphone input
- Spotipy: Spotify Web API and OAuth
- Tkinter: tiny always-on-top status pill
- Windows SAPI via pyttsx3: short voice confirmations

The first version uses one Vosk model for wake detection and commands. It downloads about 40 MB and uses roughly 300 MB RAM at runtime. It does not save microphone recordings.

## Setup

1. Use Windows 10/11 and Python 3.11.
2. Clone this repo and install dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

3. Download `vosk-model-small-en-us-0.15` from the [Vosk model list](https://alphacephei.com/vosk/models) and extract it to `models/vosk-model-small-en-us-0.15`.
4. Create an app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard). Add this Redirect URI exactly: `http://127.0.0.1:8888/callback`.
5. Copy `.env.example` to `.env` and fill in your Spotify Client ID and Client Secret. Keep `.env` private.
6. Open Spotify and sign into the account authorized for your app. Start Echo:

```powershell
.\.venv\Scripts\pythonw.exe .\run_echo.pyw
```

Or double-click `start_echo.bat`. The first Spotify authorization opens a browser.

## Commands

| Say | Action |
|---|---|
| `Echo next` / `Echo skip` | Next track |
| `Echo previous` | Previous track |
| `Echo pause` | Pause |
| `Echo resume` | Resume |
| `Echo play [song]` | Search and play a track |
| `Echo volume up` / `Echo volume down` | Change volume by 10% |
| `Echo mute` | Mute Spotify |
| `Echo what's playing` | Read the current track |

You can also say **"Echo"**, pause briefly, then say the command. Right-click the status pill to quit Echo.

## Tests

```powershell
python -m pytest
```

The automated tests cover command parsing. Live microphone recognition and Spotify playback still need to be tested on the target Windows laptop with the Vosk model and Spotify credentials.

## Notes

- Speech recognition runs locally; Spotify search and playback requests use Spotify's Web API.
- Spotify playback control requires Premium and an active Spotify Connect device.
- Spotify Development Mode apps require the owner to have Premium and require the account to be allowlisted.
- If idle CPU usage is too high, the wake detector can later be separated from Vosk and replaced with a custom openWakeWord model.
- The overlay is intentionally minimal for this first build; animation or expansion can wait until the voice path is stable.
