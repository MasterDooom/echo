# Echo

A lightweight Windows background voice controller for Spotify.

- Say **"Echo next"**, **"Echo pause"**, **"Echo resume"**, or **"Echo play [song] by [artist]"**.
- Vosk runs a restricted wake-word grammar while idle. Local Whisper `base.en` transcribes only the short command after wake-up.
- WebRTC noise suppression and voice activity detection clean the microphone signal.
- As soon as Echo hears the wake word, it briefly pauses Spotify before capturing the command. This prevents Spotify lyrics from being interpreted as commands. For next/previous/volume commands, playback resumes afterward if it was playing before. "Pause" remains paused; "play" and "resume" start playback normally.
- No Picovoice account, cloud speech recognition, LLM, or browser UI is needed. Speech processing stays on the PC; only Spotify API requests go online.

## Setup (Windows 10/11)

Python 3.14 (64-bit) is supported. From the project root:

~~~powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
~~~

The setup installs:

- **Vosk** for lightweight local wake-word detection.
- **faster-whisper** with the `base.en` model for more accurate command transcription.
- **WebRTC audio processing** for local noise suppression and voice activity detection.

Download the local speech models:

~~~powershell
python -m echo --download-model
~~~

The Vosk wake model is about 40 MB. Whisper `base.en` is a separate model download (roughly 150 MB). Both are cached under `%LOCALAPPDATA%\Echo\models`.

## Microphone and music handling

Echo uses your selected Windows microphone. Set a specific device in `.env` when needed:

~~~env
ECHO_INPUT_DEVICE=1
~~~

Echo does **not** continuously transcribe background audio. While idle, Vosk uses a restricted wake-word grammar. When it detects "Echo", Echo pauses Spotify, listens for one short command (up to 4.5 seconds), then restores playback when appropriate. This avoids trying to separate sung lyrics from your voice using a fragile loopback stream.

The brief Spotify pause is intentional. It applies to Spotify playback only. Audio from other apps can still reach the microphone; use headphones if other apps are playing speech or music.

## Connect Spotify

1. Create a personal app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/).
2. Add this Redirect URI exactly: `http://127.0.0.1:8888/callback`.
3. Copy the app's **Client ID** into `SPOTIFY_CLIENT_ID` in your local `.env`.
4. Run the one-time login:

~~~powershell
python -m echo --spotify-login
~~~

Echo uses Authorization Code with PKCE, so it does not need a Spotify client secret. Spotify playback control requires Premium and an available Spotify Connect device.

## Start Echo

~~~powershell
python -m echo
~~~

To start Echo without a console window, double-click `scripts\start_echo.vbs`. To launch it automatically when you sign in, run this once in PowerShell:

~~~powershell
.\scripts\install_startup.ps1
~~~

## Commands

| Say | Action |
|---|---|
| Echo next / Echo skip | Next track |
| Echo previous / Echo back | Previous track |
| Echo pause | Pause playback |
| Echo resume | Resume playback |
| Echo play [song] by [artist] | Search and play a track |
| Echo volume up / Echo louder | Increase volume by 10% |
| Echo volume down / Echo quieter | Decrease volume by 10% |
| Echo mute / Echo unmute | Set volume |

You can say **"Echo next"** in one phrase, or say **"Echo"**, pause briefly, then give the command. The command is transcribed locally after Spotify pauses.

If Echo cannot find a Spotify device, open Spotify on the PC and start a track once. Some Spotify Connect devices do not expose remote volume control.

## Diagnostics and tests

To test the wake detector while Spotify is playing:

~~~powershell
python scripts\diagnose_voice.py
~~~

The diagnostic does not print general speech transcripts. It only reports when the restricted wake-word detector sees "Echo", so song lyrics are not dumped to the console.

Unit tests:

~~~powershell
python -m unittest discover -s tests -v
~~~

No microphone recordings or Spotify tokens are committed to this repository.

## Technical references

- [Vosk models and accuracy trade-offs](https://alphacephei.com/vosk/models)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [WebRTC audio processing and VAD](https://github.com/strands-labs/pywebrtc-audio)
- [Spotify Authorization Code with PKCE](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow)
- [Spotify playback controls](https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback)
