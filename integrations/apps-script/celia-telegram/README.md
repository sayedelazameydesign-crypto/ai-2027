# CELIA → Telegram (Google Apps Script)

Direct, free integration between your Google Apps Script project and
@CeliaFashionBot via the Telegram Bot API. No server, no billing.

Target project (set as `APPS_SCRIPT_ID` secret):
`1UNRaWAvGNYI6lYnJ1nlQt3PyMsG3eZkI--WASZrhMPoBzB6ZqiBXG-5z`

## Path A — paste (instant, no credentials)

1. Open the project:
   https://script.google.com/home/projects/1UNRaWAvGNYI6lYnJ1nlQt3PyMsG3eZkI--WASZrhMPoBzB6ZqiBXG-5z/edit
2. Replace the default `myFunction(){}` stub with the contents of
   [`Code.gs`](./Code.gs).
3. Set `BOT_TOKEN` (from @BotFather) and `CHAT_ID` (from the bot's
   "chat id الخاص بك: <number>" reply) at the top of the file.
4. Run `testSendTelegram()` once — a test message arrives in Telegram.

## Path B — deploy from this repo (automatic, needs one secret)

The repo has a deploy workflow that pushes `Code.gs` to your project via
[clasp](https://github.com/google/clasp). One-time setup:

1. `npm i -g @google/clasp`
2. `clasp login` and approve the Google account that owns the project.
3. Copy the contents of `~/.clasprc.json` into a **repository secret**
   named `CLASPRC_JSON`.
4. Ensure the `APPS_SCRIPT_ID` secret holds your Script ID
   (Project Settings → Script ID).
5. Run **Deploy CELIA Telegram (Apps Script)** from `main` with a label.

The workflow does `clasp push --force` (overwrites the project, including the
default stub) and records a version with your label. `CLASPRC_JSON` is written
to disk only for the duration of the run (mode 600) and removed afterwards; it
is never logged.

## Notes

- The CI bot (`scripts/approval_bot_ci.py`) also polls `getUpdates`. If you use
  `discoverChatId()`, run it when the CI bot is idle, or just read the chat id
  from the bot's Telegram reply instead.
- `BOT_TOKEN` is a secret — never commit or share it.
