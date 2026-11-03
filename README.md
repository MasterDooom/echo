# Echo

A lightweight Windows background voice controller for Spotify.

- Say "Echo next", "Echo pause", "Echo resume", or "Echo play [song] by [artist]".
- Porcupine detects the custom wake word locally. Vosk only handles speech after activation.
- Spotify playback and track search use Spotify's Web API with OAuth PKCE.
- A small animated orb sits at the bottom-right of the desktop. Click it to expand the status pill; right-click for pause/quit.
- No cloud speech recognition, LLM, browser UI, or always-open Spotify window is required.

## Setup (Windows 10/11)

Use Python 3.12 (64-bit).

~~~powershell
git clone https://github.com/MasterDooom/echo.git
cd echo
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
~~~

### 1. Configure the wake word

1. Create a free account at https://console.picovoice.ai/.
2. Copy your AccessKey into PICOVOICE_ACCESS_KEY in .env.
3. In the Porcupine console, create a Windows custom wake-word model for Echo.
4. Save the downloaded .ppn file as assets/echo_windows.ppn.

Porcupine runs on-device; the access key authorizes the SDK. Keep the key and .ppn out of Git. If Echo opens the wrong microphone, set ECHO_INPUT_DEVICE in .env to a device-name fragment or index.

### 2. Connect Spotify

1. Create a personal app in the Spotify Developer Dashboard: https://developer.spotify.com/dashboard.
2. Add this Redirect URI exactly: http://127.0.0.1:8888/callback.
3. Copy the app's Client ID into SPOTIFY_CLIENT_ID in .env.
4. Run the one-time login:

~~~powershell
python -m echo --spotify-login
~~~

Echo uses Authorization Code with PKCE, so it does not need a Spotify client secret. Spotify Development Mode currently requires the app owner to have Premium and limits an app to five authorized users. This is intended for personal use.

### 3. Start Echo

~~~powershell
python -m echo
~~~

The small English Vosk model (about 40 MB) downloads to %LOCALAPPDATA%\Echo\models the first time. Audio is processed locally; Spotify receives only API requests for search and playback.

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
| Echo volume up / Echo quieter | Change Spotify device volume |
| Echo mute / Echo unmute | Set volume |

Spotify playback controls require Premium and a Spotify Connect device to be available. Open Spotify on the PC and start a track once if Echo cannot find a controllable device. Some devices do not expose remote volume control.

## Development

~~~powershell
python -m unittest discover -s tests -v
~~~

The first version intentionally keeps the command set deterministic and small. No audio recordings or OAuth secrets are committed to this repository.

## Technical references

- Porcupine Python quick start: https://picovoice.ai/docs/quick-start/porcupine-python/
- Vosk offline speech recognition: https://github.com/alphacep/vosk-api
- Spotify Authorization Code with PKCE: https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow
- Spotify playback controls: https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback
- Spotify Development Mode restrictions: https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide
