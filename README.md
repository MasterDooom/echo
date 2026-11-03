# Echo

A lightweight Windows background voice controller for Spotify.

- Say **"Echo next"**, **"Echo pause"**, **"Echo resume"**, or **"Echo play [song] by [artist]"**.
- Vosk runs a restricted wake-word grammar while idle. A more capable local Whisper model only transcribes the short command after wake-up.
- WebRTC AEC3 uses the Windows speaker loopback as a reference to suppress Spotify/system audio leaking into the microphone. Noise suppression and VAD run on the cleaned microphone signal.
- Spotify search and playback use the Spotify Web API with OAuth PKCE.
- A small animated orb sits at the bottom-right of the desktop. Click it to expand the status pill; right-click for pause/quit.
- No Picovoice account, cloud speech recognition, LLM, or browser UI is needed. Audio stays on the PC; only Spotify API requests go online.

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
- **faster-whisper** with the base.en model for better command transcription. The model is downloaded once and then stays local.
- **WebRTC AEC3** for speaker echo cancellation and noise suppression.
- **SoundCard** for Windows WASAPI speaker-loopback capture.

Download the local speech models:

~~~powershell
python -m echo --download-model
~~~

The Vosk wake model is about 40 MB. Whisper base.en is a separate model download (roughly 150 MB). Both are cached under %LOCALAPPDATA%\Echo\models.

## Microphone and music bleed

Echo uses the Windows default output device as the AEC reference. The overlay reports whether speaker echo cancellation is active. For best results, use the same Windows default output device for Spotify and system audio.

Set a specific microphone in .env when needed:

~~~env
ECHO_INPUT_DEVICE=1
~~~

If the overlay warns that speaker echo cancellation is unavailable, Echo can still listen, but use headphones while Spotify is playing. WASAPI loopback is what lets the AEC distinguish speaker playback from your voice; noise suppression alone cannot reliably remove song vocals.

## Connect Spotify

1. Create a personal app in the Spotify Developer Dashboard.
2. Add this Redirect URI exactly: http://127.0.0.1:8888/callback.
3. Copy the app's Client ID into SPOTIFY_CLIENT_ID in your local .env.
4. Run the one-time login:

~~~powershell
python -m echo --spotify-login
~~~

Echo uses Authorization Code with PKCE, so it does not need a Spotify client secret. Spotify playback control requires Premium and an available Spotify Connect device.

## Start Echo

~~~powershell
python -m echo
~~~

To start Echo without a console window, double-click scripts\start_echo.vbs. To launch it automatically when you sign in, run this once in PowerShell:

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

You can say **"Echo next"** in one phrase, or say **"Echo"**, pause briefly, then give the command. Only the restricted wake-word recognizer is active while idle. After wake-up, Echo captures one short utterance (up to 4.5 seconds) and transcribes the cleaned audio locally.

If Echo cannot find a Spotify device, open Spotify on the PC and start a track once. Some Spotify Connect devices do not expose remote volume control.

## Diagnostics and tests

To see what reaches the cleaned microphone while Spotify is playing:

~~~powershell
python scripts\diagnose_voice.py
~~~

For a useful test, run the diagnostic with a Spotify track playing: it should not print song lyrics. Then say "Echo next" and confirm it recognizes your voice.

Unit tests:

~~~powershell
python -m unittest discover -s tests -v
~~~

No microphone recordings or Spotify tokens are committed to this repository.

## Technical references

- Vosk models and accuracy trade-offs: https://alphacephei.com/vosk/models
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- WebRTC audio processing (AEC3, noise suppression, VAD): https://github.com/strands-labs/pywebrtc-audio
- SoundCard WASAPI loopback capture: https://github.com/bastibe/SoundCard
- Spotify Authorization Code with PKCE: https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow
- Spotify playback controls: https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback
