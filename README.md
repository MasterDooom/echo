# Echo

A lightweight Windows background voice controller for Spotify.

- Say **"Echo next"**, **"Echo pause"**, **"Echo resume"**, or **"Echo play [song] by [artist]"**.
- Vosk detects the wake word and recognizes commands locally. It listens only for the restricted wake vocabulary while idle, then switches to normal transcription after activation.
- Spotify search and playback use the Spotify Web API with OAuth PKCE.
- A small animated orb sits at the bottom-right of the desktop. Click it to expand the status pill; right-click for pause/quit.
- No Picovoice account, company email, cloud speech recognition, LLM, or browser UI is needed.

## Setup (Windows 10/11)

Use Python 3.12 (64-bit).

~~~powershell
git clone -b echo-lightweight-prototype https://github.com/MasterDooom/echo.git
cd echo
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
~~~

The small English Vosk model (about 40 MB) downloads automatically to %LOCALAPPDATA%\Echo\models the first time you launch Echo. It runs locally and is not stored in the repository.

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

You can say **"Echo next"** in one phrase, or say **"Echo"**, pause briefly, then give the command. The command window stays open for up to five seconds after the wake word.

If Echo cannot find a Spotify device, open Spotify on the PC and start a track once. Some Spotify Connect devices do not expose remote volume control.

## Development

~~~powershell
python -m unittest discover -s tests -v
~~~

The unit tests cover command parsing and track matching. Live microphone recognition and Spotify playback still need to be tested on the target Windows laptop. No microphone recordings or Spotify tokens are committed to this repository.

## Technical references

- [Vosk offline speech recognition](https://github.com/alphacep/vosk-api)
- [Vosk installation and microphone examples](https://alphacephei.com/vosk/install)
- [Spotify Authorization Code with PKCE](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow)
- [Spotify playback controls](https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback)
