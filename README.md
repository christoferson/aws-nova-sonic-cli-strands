# aws-nova-sonic-cli-strands

Console speech-to-speech AI agent using [Amazon Nova 2.5 Sonic](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-2-5-sonic.html) on Amazon Bedrock, built with the [Strands Agents](https://strandsagents.com/docs/user-guide/sdk/bidi/) `BidiAgent`. You speak into your microphone, the model answers out loud, and both transcripts are printed in the terminal.

Sister project of [aws-nova-sonic-cli](https://github.com/christoferson/aws-nova-sonic-cli), which implements the same assistant by hand against the raw bidirectional event protocol. Here Strands handles the stream, barge-in, tool calls, audio devices and reconnects, so the script is mostly the prompt, the tools and the display.

## Project layout

```
nova_sonic_strands.py   Main program: voice assistant with barge-in and tools (date/time, calculator)
pyproject.toml          Dependencies (uv)
uv.lock                 Locked versions
requirements.txt        Pinned dependencies for pip, exported from uv.lock
```

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 for you if it's missing)
- A microphone and speakers. **Use headphones**, otherwise the model can hear itself and interrupt its own answer.
- An AWS account with access to `amazon.nova-2-5-sonic` in Amazon Bedrock, in a region where it's available (default `us-east-1`; also `us-west-2`, `eu-north-1`, `ap-northeast-1`).
- IAM permission `bedrock:InvokeModelWithBidirectionalStream`.

## Setup

```bash
uv sync
```

This creates `.venv` with Python 3.12 (pinned in `.python-version`) and installs the dependencies from `uv.lock`.

Without uv, use the pinned `requirements.txt` (Python 3.12+):

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
python nova_sonic_strands.py
```

After changing dependencies with `uv add`, regenerate it:

```bash
uv export --format requirements-txt --no-hashes --no-dev -o requirements.txt
```

## AWS credentials and region

Credentials are resolved by boto3 from your AWS profile, so no keys need to be pasted into the shell. The script passes a `boto3.Session` to Strands, so SSO and assume-role profiles work too.

Profile selection: `--profile <name>`, else `$AWS_PROFILE`, else `default`.

For an SSO profile, log in first:

```bash
aws sso login --profile <your-profile>
```

Region selection: `--region <region>`, else `$AWS_REGION`, else `us-east-1`. The profile's configured region is **not** used, because Nova Sonic is only available in some regions.

Git Bash:

```bash
export AWS_PROFILE=<your-profile>
export AWS_REGION=us-east-1
```

PowerShell:

```powershell
$env:AWS_PROFILE="<your-profile>"
$env:AWS_REGION="us-east-1"
```

## Run

```bash
uv run nova_sonic_strands.py           # barge-in + tools (date/time, calculator)
uv run nova_sonic_strands.py --debug   # with Strands debug logging
```

Then start talking. Press **Ctrl+C** to end the session.

What you say is printed in cyan and the assistant's reply in green. You can start speaking while the assistant is talking: it stops, `(interrupted)` is printed in grey, and it answers your new input. The assistant's text arrives ahead of its voice, so the printed reply may include words it never got to say.

Options:

| Flag | Default | Description |
| --- | --- | --- |
| `--profile` | `$AWS_PROFILE` or `default` | AWS profile for credentials |
| `--region` | `$AWS_REGION` or `us-east-1` | Bedrock region |
| `--model-id` | `amazon.nova-2-5-sonic` | Model ID. `amazon.nova-2-sonic-v1:0` (Nova 2 Sonic) also works |
| `--voice` | `tiffany` | Output voice (see below) |
| `--list-voices` | | Print available voices and exit |
| `--system-prompt` | voice assistant prompt | Replace the default prompt. The default tells the model it is speaking out loud, matches its gender to the voice, and has it reply in the language you speak |
| `--debug` | off | Strands debug logging: events, tool calls, connection restarts (per-chunk audio lines are hidden) |

Example:

```bash
uv run nova_sonic_strands.py --voice matthew --system-prompt "You are a friendly travel guide. Keep answers short."
```

## Tools

Tools are plain Python functions with the Strands `@tool` decorator in `nova_sonic_strands.py`. Strands builds each tool's spec from its signature and docstring, and the model reads the docstring to decide when to call it. Each call is printed in grey as `(using name)`.

| Tool | What it does | Try saying |
| --- | --- | --- |
| `get_date_and_time` | Current date, time and weekday, locally or in any IANA timezone | "What time is it in New York?" |
| `calculate` | Exact arithmetic (`+ - * / // % **`, `sqrt`, `log`, trig, `pi`, `e`), parsed safely without `eval` | "What's 15 percent of 2,400?" |

Tools run in the background, so the conversation keeps going while they work, and they keep running if you interrupt. The tool list is kept small on purpose: every tool makes barge-in feel a little less responsive.

To add a tool, write a function with `@tool`, type hints and a docstring (with an `Args:` section), then add it to `tools=[...]` in `main()`. Mention it in `TOOL_PROMPT_SUFFIX` so the model uses it instead of guessing.

## Long conversations

Nova Sonic closes every connection after about 8 minutes. Strands reconnects automatically after 7 minutes, waiting for a pause between turns, and replays the conversation so far as text, so you can keep talking.

## Voices

Voice IDs from the Nova 2 Sonic language-support docs. `tiffany` has been tested with Nova 2.5 Sonic.

| Voice | Locale | Notes |
| --- | --- | --- |
| `matthew` | en-US | masculine, polyglot (speaks all supported languages) |
| `tiffany` | en-US | feminine, polyglot (speaks all supported languages) |
| `amy` | en-GB | feminine |
| `olivia` | en-AU | feminine |
| `kiara` | en-IN / hi-IN | feminine |
| `arjun` | en-IN / hi-IN | masculine |
| `ambre` / `florian` | fr-FR | feminine / masculine |
| `beatrice` / `lorenzo` | it-IT | feminine / masculine |
| `tina` / `lennart` | de-DE | feminine / masculine |
| `lupe` / `carlos` | es-US | feminine / masculine |
| `carolina` / `leo` | pt-BR | feminine / masculine |

## Audio format

- Input: 16 kHz, 16-bit, mono PCM (microphone)
- Output: 24 kHz, 16-bit, mono PCM (speakers)

## Troubleshooting

- **`That AWS profile was not found`**: check `--profile` or `$AWS_PROFILE` against `~/.aws/config`.
- **`Could not get AWS credentials`**: the profile has no credentials, or (for SSO) the login expired; run `aws sso login --profile <name>`. Add `--debug` to see the underlying error.
- **`AccessDeniedException`**: model access isn't enabled for your account in that region, or the IAM policy lacks `bedrock:InvokeModelWithBidirectionalStream`.
- **No sound or wrong mic**: the default Windows input/output devices are used; change them in Windows Sound settings.
- **Model interrupts itself**: use headphones to avoid echo.
- **`?` instead of some characters**: the console's code page can't display them, so they are replaced instead of crashing the script.
