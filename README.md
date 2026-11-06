# Echo

A lightweight, local-first Windows voice controller for Spotify.

Echo is designed to stay in the background as a small orb near the taskbar. Say the wake name (pronounced **AY-koh**) followed by a command.

## What it does

- Vosk is used only as a low-cost wake candidate generator until a custom openWakeWord Echo model is configured.
- A speech gate and temporal stabilizer reject isolated wake candidates; local Whisper independently verifies the wake phrase before Echo is allowed to pause Spotify.
- faster-whisper small.en (CPU int8) transcribes the command; audio is passed to Whisper from the original microphone PCM, not the noise-suppressed wake detector stream.
- If the command is empty, unclear or invalid, Echo restores Spotify playback if it paused a previously playing track.
- A deterministic local intent parser distinguishes playback actions, track requests, playlist requests and queue requests.
- Spotify search resolves tracks using title, artist and version metadata.
- Your Spotify playlists are fetched, paginated and cached locally for name matching.
- Spotify playback commands use the official Web API and keep targeting the device that was active when Echo woke.
- The microphone stays on-device. No speech audio is uploaded or saved to disk.

## Setup

Windows 10/11 and Python 3.14 (64-bit) are supported.

Run these commands from the repository root:

~~~powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
~~~

Add your Spotify Developer App Client ID to .env:

~~~env
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
~~~

In the Spotify Developer Dashboard, add the redirect URI exactly as shown above.

## Spotify permissions

Echo uses Authorization Code with PKCE; no Spotify Client Secret is needed.

The current permission set includes:

- user-modify-playback-state
- user-read-playback-state
- user-read-currently-playing
- playlist-read-private
- playlist-read-collaborative

Run the login command after setup, or whenever Echo reports that permissions need updating:

~~~powershell
python -m echo --spotify-login
~~~

This opens Spotify's consent screen. Re-login is required because the older token may not include playlist and queue-reading permissions. The local playlist cache is cleared after re-login so it cannot carry over from a different account.

Spotify playback control and queue insertion require Premium and an available Spotify Connect device. Spotify Development Mode apps also require the app owner to have Premium.

## Download speech models

~~~powershell
python -m echo --download-model
~~~

## Wake-word detection

The default setup uses Vosk only to propose a wake candidate. WebRTC VAD gates it, consecutive detections are smoothed, and an unbiased local Whisper pass must confirm a wake phrase at the beginning of the utterance before Spotify is paused. Ordinary mentions of "echo" later in a sentence are rejected.

For a dedicated streaming keyword spotter, Echo supports openWakeWord. A public model for the exact phrase **"Echo"** is available in the [openWakeWord model library](https://openwakeword.com/library/4929). Sign in there, download the ONNX file `Echo_20260930_221231.onnx`, and place it under `%LOCALAPPDATA%\Echo\models`.

Published benchmark results for that model are modest: 55.2% recall in clean speech, 38.8% with background noise/room reverb, and 0.65 false activations per hour at threshold 0.5. Echo uses score smoothing, VAD and independent local Whisper confirmation on top of the detector. These checks reduce false triggers but cannot recover a wake phrase missed by the model.

Configure the downloaded file in `.env`:

~~~env
ECHO_WAKE_MODEL_PATH=%LOCALAPPDATA%\Echo\models\Echo_20260930_221231.onnx
ECHO_WAKE_THRESHOLD=0.50
~~~

A different public model, "Hey Echo", has higher reported recall but requires saying **"Hey Echo"** and has a higher false-activation rate; Echo does not select it by default. The openWakeWord training notebook can be used to fine-tune a wake phrase for your own pronunciation: https://colab.research.google.com/drive/1q1oe2zOyZp7UsB3jJiQ1IFn8z5YfjwEb?usp=sharing

The ONNX model must be downloaded manually because the library requires sign-in. It is licensed for personal and non-commercial use by default. The openWakeWord source code is Apache-2.0; review model-asset licensing before commercial distribution.

Porcupine was also evaluated but not selected as the default: custom wake words require a Picovoice AccessKey and a `.ppn` model, and Picovoice says it has no dedicated personal/non-commercial plan.
The Vosk wake-word model and Whisper small.en model are downloaded once and stored under %LOCALAPPDATA%\Echo\models. Speech recognition runs locally after the downloads complete.

To verify the microphone transcription separately from Spotify, pause Spotify and run:

~~~powershell
python -m echo --test-speech
~~~

To test wake-word candidates and local confirmation without controlling Spotify, run:

~~~powershell
python -m echo --test-wake
~~~

The wake diagnostic listens for 60 seconds, reports accepted and rejected candidates, saves no audio and never sends microphone data to cloud services. Speak normally for a few seconds, then say "Echo, play Starboy".

The speech diagnostic records one short utterance in RAM only, then prints the local transcript, parsed intent and audio-level diagnostics. It does not save or upload the recording. If the transcript is wrong, the issue is microphone capture or speech recognition; if the transcript is right but the intent is wrong, the issue is command parsing.

## Start Echo

~~~powershell
python -m echo
~~~

The orb has three important states:

- **Listening:** the microphone is active and local wake-word detection is running.
- **Working:** Echo is processing a command.
- **Microphone off:** privacy mode has stopped and closed the microphone input stream.

Click the orb to expand it. Click the right-side ON/OFF control to toggle privacy mode. Right-click the orb for the same privacy action or to quit Echo.

To refresh your Spotify playlist cache manually:

~~~powershell
python -m echo --refresh-playlists
~~~

To start Echo without a console window, double-click scripts\start_echo.vbs. To add it to Windows startup, run scripts\install_startup.ps1 once in PowerShell.

## Voice commands

### Playback

| Say | Action |
|---|---|
| Echo next / Echo skip / Echo play the next | Next track |
| Echo previous / Echo go back | Previous track |
| Echo pause | Pause playback |
| Echo resume | Resume playback |
| Echo play Blinding Lights by The Weeknd | Search and play a track |
| Echo play The Next Episode by Dr. Dre | Search and play the named track |

### Queue

| Say | Action |
|---|---|
| Echo queue Starboy | Add the track to the playback queue |
| Echo add Starboy to my queue | Add the track to the playback queue |
| Echo what's next | Show the upcoming queue |
| Echo what's in my queue | Show the upcoming queue |

Queue insertion is separate from editing a playlist. Spotify's Web API supports adding a track to the current playback queue and reading the queue; it does not expose queue removal or arbitrary queue reordering through these endpoints.

### Playlists

| Say | Action |
|---|---|
| Echo play my workout playlist | Match and play a personal playlist |
| Echo play workout | Prefer a strong match against your playlists, then search tracks |
| Echo shuffle my workout playlist | Play that playlist with shuffle enabled |

Echo reads your Spotify playlist names into a small local cache. It does not download playlist audio.

### Shuffle, repeat, volume and seeking

| Say | Action |
|---|---|
| Echo shuffle on / Echo shuffle off | Enable or disable shuffle |
| Echo repeat this song | Repeat the current track |
| Echo repeat playlist | Repeat the current context |
| Echo repeat off | Disable repeat |
| Echo volume up / Echo louder | Increase volume by 10% |
| Echo volume down / Echo quieter | Decrease volume by 10% |
| Echo set volume to 35 percent | Set volume to 35% |
| Echo mute / Echo unmute | Mute or restore volume |
| Echo forward 30 seconds | Seek forward |
| Echo rewind 15 seconds | Seek backward |
| Echo seek to 1:25 | Seek to an absolute position |

### Playback information

| Say | Action |
|---|---|
| Echo what's playing | Identify the current track and artist |
| Echo what's next | Show the queue |

If Spotify returns multiple similarly likely tracks, Echo asks for a clearer title/artist instead of deliberately playing a weak match. Adding the artist or requested version (acoustic, live, remix, etc.) improves resolution.

## Microphone privacy

The microphone must remain active while Echo is waiting for a wake phrase; otherwise it cannot hear the wake name. This is local microphone capture, not audio uploaded to a website.

While idle:

- PortAudio captures microphone frames locally.
- WebRTC performs local noise suppression and voice activity detection.
- The configured wake backend runs locally. By default, Vosk proposes candidates; optional openWakeWord runs a custom ONNX model.
- WebRTC VAD gates wake candidates and a temporal stabilizer rejects one-frame spikes.
- Whisper runs only to verify a candidate and capture a command; it does not run continuously while idle.
- Audio frames are held briefly in RAM only; they are not recorded to files or sent to cloud speech services.

After a wake candidate:

1. Local Whisper verifies that the wake phrase is at the beginning of the utterance.
2. Only a verified wake is allowed to pause Spotify.
3. Whisper transcribes the command and the deterministic parser resolves its intent.
4. If no command follows, transcription is uncertain, parsing fails or Spotify throws an error, playback is restored if it was playing beforehand.
5. An intentional pause remains paused; a successful play/resume command establishes the new playback state.

**Privacy mode** closes the microphone stream and releases the input device. The orb changes to **MICROPHONE OFF**. Reactivate listening from the orb when ready.

Spotify authentication, playlist lookup, search and playback commands do use the internet because they call Spotify's service. Voice audio and transcripts do not.

## Tests

Run the unit suite:

~~~powershell
python -m unittest discover -s tests -v
~~~

The CI workflow runs the tests and compiles the package on Python 3.12 and 3.14.

The repository tests cover command parsing, wake-word aliases, local privacy-mode release, track matching, queue insertion, queue reading, playlist pagination/caching, playback device targeting and Spotify response handling. Live microphone quality and real Spotify playback still need a final manual test on the target Windows laptop.

## Technical references

- Vosk offline speech recognition: https://github.com/alphacep/vosk-api
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- WebRTC audio processing: https://github.com/strands-labs/pywebrtc-audio
- Spotify Search: https://developer.spotify.com/documentation/web-api/reference/search
- Spotify current user's playlists: https://developer.spotify.com/documentation/web-api/reference/get-a-list-of-current-users-playlists
- Spotify Add to Queue: https://developer.spotify.com/documentation/web-api/reference/add-to-queue
- Spotify Get Queue: https://developer.spotify.com/documentation/web-api/reference/get-queue
- Spotify scopes: https://developer.spotify.com/documentation/web-api/concepts/scopes
