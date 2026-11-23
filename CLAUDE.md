 # aws-nova-sonic-cli-strands

  Console speech-to-speech assistant on Amazon Nova 2.5 Sonic, built with the Strands Agents
  framework (BidiAgent). Sister project of https://github.com/christoferson/aws-nova-sonic-cli, which implements the
  same features by hand against the raw bidirectional event protocol. That repo is the
  reference for behavior, and its docs/nova-2-sonic/ folder has offline copies of the Nova 2 Sonic docs.

  ## Environment
  - Windows 10, Git Bash. Python 3.12 via uv (`uv sync`, `uv run ...`). Never use pip directly.
  - AWS credentials come from the AWS profile (AWS_PROFILE) via boto3. Region us-east-1.
    Never ask for, print, or hard-code access keys.
  - Model: amazon.nova-2-5-sonic (latest; amazon.nova-2-sonic-v1:0 via --model-id). Default voice: tiffany.
  - Audio uses PyAudio. Tell the user to wear headphones, because speaker echo triggers barge-in.

  ## Strands specifics
  - The Nova Sonic support lives in `strands.bidi` (`strands.experimental.bidi` is a deprecated
    alias). Before writing code against it, read the installed package source in .venv to confirm
    class names, constructor arguments and defaults. Docs and blog examples may be out of date.
  - The model class is `BedrockNovaSonicModel`. Pass `boto_session=boto3.Session(...)` for
    credentials and region; always pass `model_id` and `voice` (its defaults are not ours).
  - Cross-modal text input only got a reply while audio was streaming (mic or silence); seen on Nova 2 Sonic.

  ## Lessons carried over from aws-nova-sonic-cli
  - Default system prompt: say it's a voice assistant speaking out loud, match its gender to
    the voice (tiffany = female), and tell it to reply in the user's language.
  - With tools, keep a prompt hint ("You have tools for X and Y. Use them instead of guessing").
    Without it the model guesses, e.g. the time.
  - Tools make barge-in feel less responsive. Keep the tool list small.
  - Calculator tools must never use eval(); parse with ast and whitelist operations.
    (strands_tools.calculator uses SymPy, which evaluates code; don't use it.)
  - Windows consoles can crash on some characters: reconfigure stdout with errors="replace".
  - Ctrl+C must shut down cleanly (close audio devices and the stream, no traceback).

  ## Working style
  - User-facing console output: short, pre-written sentences about what the assistant can do.
    No counts, file paths or internals; developer detail goes behind --debug.
  - Don't add libraries or code just for cosmetic output.
  - After creating a feature, explain in plain language what it shows and how to try it.
  - Stage changes, but never commit unless asked. Never stage chat history or recordings
    (chat.json, *.wav, session logs).
  - Check documentation claims against the installed code or the saved docs; don't guess
    APIs, model IDs or prices.