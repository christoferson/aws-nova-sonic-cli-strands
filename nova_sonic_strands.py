"""Amazon Nova 2.5 Sonic voice assistant on Strands BidiAgent: speech-to-speech with barge-in and tools (date/time, calculator)."""
import argparse
import ast
import asyncio
import datetime
import logging
import math
import operator
import os
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import boto3
from rich.console import Console
from strands import tool
from strands.bidi import BidiAgent
from strands.bidi.io import AudioIO, ConsoleIO
from strands.bidi.models import BedrockNovaSonicModel
from strands.bidi.types.events import (
    BidiBargeInEvent,
    BidiToolUseBlocksEvent,
    BidiTranscriptDeltaEvent,
    BidiTranscriptStartEvent,
    BidiTranscriptStopEvent,
)

# Legacy console code pages (e.g. cp932) can't encode every character the model may output.
sys.stdout.reconfigure(errors="replace")

# Nova 2.5 Sonic is the latest Nova speech-to-speech model; amazon.nova-2-sonic-v1:0 still works via --model-id.
DEFAULT_MODEL_ID = "amazon.nova-2-5-sonic"
DEFAULT_VOICE = "tiffany"
OUTPUT_SAMPLE_RATE = 24000

# https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-language-support.html
VOICES = {
    "matthew": "en-US, masculine, polyglot (speaks all supported languages)",
    "tiffany": "en-US, feminine, polyglot (speaks all supported languages)",
    "amy": "en-GB, feminine",
    "olivia": "en-AU, feminine",
    "kiara": "en-IN / hi-IN, feminine",
    "arjun": "en-IN / hi-IN, masculine",
    "ambre": "fr-FR, feminine",
    "florian": "fr-FR, masculine",
    "beatrice": "it-IT, feminine",
    "lorenzo": "it-IT, masculine",
    "tina": "de-DE, feminine",
    "lennart": "de-DE, masculine",
    "lupe": "es-US, feminine",
    "carlos": "es-US, masculine",
    "carolina": "pt-BR, feminine",
    "leo": "pt-BR, masculine",
}

TOOL_PROMPT_SUFFIX = (
    " You have tools for the current date and time and for exact calculations. "
    "Use them instead of guessing."
)

# Debug log lines emitted for every audio chunk; dropped so --debug stays readable.
NOISY_LOG_PREFIXES = (
    "audio_bytes=",
    "raw_event_size=",
    "event_keys=",
    "converted_event_type=",
    "audio_byte_count=",
)


def default_system_prompt(voice_id=DEFAULT_VOICE):
    # Based on https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-system-prompts.html
    # Gender must match the voice for languages with gender agreement (French, Hindi, Spanish, ...).
    gender = "female" if "feminine" in VOICES.get(voice_id, "") else "male"
    return (
        f"You are a warm, professional, and helpful {gender} AI voice assistant. "
        "You are talking with the user out loud in real time: they hear your voice and you hear theirs. "
        "Give accurate answers that sound natural, direct, and human. Start by answering the user's "
        "question clearly in 1-2 sentences. Then, expand only enough to make the answer understandable, "
        "staying within 3-5 short sentences total. Avoid sounding like a lecture or essay. "
        "Always reply in the language the user is speaking, and do not mix in English unless the user "
        "speaks English."
    )


# ---- tools ----
# Strands builds each tool's spec from the signature and docstring; the model reads the docstring
# to decide when to call it. Returned dicts are sent to the model as JSON.

@tool
def get_date_and_time(timezone: str | None = None) -> dict:
    """Get the current date, time and day of the week. Use this whenever the user asks about
    the current time or date, or for anything relative to today.

    Args:
        timezone: IANA timezone name such as 'Asia/Tokyo' or 'America/New_York'. Omit for the user's local time.
    """
    if timezone:
        try:
            now = datetime.datetime.now(ZoneInfo(timezone))
        except (ZoneInfoNotFoundError, ValueError):
            return {"error": f"Unknown timezone '{timezone}'. Use an IANA name like 'Asia/Tokyo'."}
    else:
        now = datetime.datetime.now().astimezone()
    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "dayOfWeek": now.strftime("%A"),
        "timezone": timezone or "local",
        "utcOffset": now.isoformat(timespec="minutes")[-6:],
    }


_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "log": math.log, "log10": math.log10,
    "exp": math.exp, "sin": math.sin, "cos": math.cos, "tan": math.tan,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}
MAX_EXPRESSION_LENGTH = 200
MAX_EXPONENT = 100
MAX_RESULT_BITS = 4096


def _evaluate(node):
    """Evaluate a parsed expression tree, allowing only whitelisted node types.

    The expression comes from the model (ultimately from what a user said), so eval() would let
    it run arbitrary code. ast.parse() only builds a syntax tree; we walk it ourselves and reject
    anything that isn't a number, a constant, an arithmetic operator, or a listed math function.
    """
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_EXPONENT:
                raise ValueError(f"exponent larger than {MAX_EXPONENT} is not allowed")
            # Chained powers like (9**99)**99 stay under MAX_EXPONENT but explode in size.
            if abs(left) > 1 and right > 0 and right * math.log2(abs(left)) > MAX_RESULT_BITS:
                raise ValueError("result is too large")
        return _BIN_OPS[type(node.op)](left, right)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCTIONS
        and not node.keywords
    ):
        return _FUNCTIONS[node.func.id](*(_evaluate(arg) for arg in node.args))
    raise ValueError("only numbers, + - * / // % **, parentheses, pi, e and basic math functions are allowed")


@tool
def calculate(expression: str) -> dict:
    """Evaluate an arithmetic expression exactly. Use this for any math beyond trivial mental
    arithmetic, e.g. '1234 * 5678', '(15 / 100) * 2400', 'sqrt(2)'.

    Args:
        expression: Expression using numbers, + - * / // % ** ( ), pi, e, sqrt, abs, round, log, log10, exp, sin, cos, tan
    """
    if len(expression) > MAX_EXPRESSION_LENGTH:
        return {"error": "expression is too long"}
    try:
        result = _evaluate(ast.parse(expression.replace("^", "**"), mode="eval"))
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
        return {"expression": expression, "error": str(e) or type(e).__name__}
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        result = int(result)
    elif isinstance(result, float):
        result = round(result, 10)
    return {"expression": expression, "result": result}


# ---- transcript display ----

ROLE_STYLES = {"user": "cyan", "assistant": "green"}
NOTE_STYLE = "bright_black"


class _TranscriptPrinter:
    """Output stream that prints transcripts as colored lines: cyan for you, green for the assistant."""

    def __init__(self):
        self._console = Console(soft_wrap=True, highlight=False)
        self._open_line = None  # content_id of the transcript line being printed

    async def start(self, agent):
        pass

    async def stop(self):
        self._end_line()

    async def __call__(self, event):
        if isinstance(event, BidiTranscriptStartEvent):
            self._end_line()
            self._open_line = event.content_id
        elif isinstance(event, BidiTranscriptDeltaEvent) and event.content_id == self._open_line:
            self._console.print(event.delta, style=ROLE_STYLES.get(event.role), end="")
        elif isinstance(event, BidiTranscriptStopEvent) and event.content_id == self._open_line:
            self._end_line()
        elif isinstance(event, BidiBargeInEvent):
            if self._open_line:
                self._console.print(" (interrupted)", style=NOTE_STYLE, end="")
                self._end_line()
            else:
                self._console.print("(interrupted)", style=NOTE_STYLE)
        elif isinstance(event, BidiToolUseBlocksEvent):
            self._end_line()
            for tool_use in event.tool_uses:
                self._console.print(f"(using {tool_use['name']})", style=NOTE_STYLE)

    def _end_line(self):
        if self._open_line:
            self._console.print()
            self._open_line = None


class TranscriptConsole(ConsoleIO):
    """ConsoleIO whose output is the colored transcript printer instead of Strands' boxed display."""

    def output(self):
        return _TranscriptPrinter()


# ---- app ----

class _DropAudioChunkLogs(logging.Filter):
    def filter(self, record):
        return not (isinstance(record.msg, str) and record.msg.startswith(NOISY_LOG_PREFIXES))


def configure_logging(debug):
    handler = logging.StreamHandler()
    handler.addFilter(_DropAudioChunkLogs())
    handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d [debug] %(name)s: %(message)s", "%H:%M:%S"))
    logging.basicConfig(level=logging.WARNING, handlers=[handler])
    if debug:
        logging.getLogger("strands").setLevel(logging.DEBUG)


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Amazon Nova 2.5 Sonic voice assistant with barge-in and tools")
    parser.add_argument("--profile", help="AWS profile name (defaults to $AWS_PROFILE, then 'default')")
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="Bedrock region (defaults to $AWS_REGION, then us-east-1)",
    )
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument(
        "--voice",
        default=DEFAULT_VOICE,
        choices=VOICES,
        metavar="VOICE",
        help=f"Output voice ID (default: {DEFAULT_VOICE}). See --list-voices",
    )
    parser.add_argument("--list-voices", action="store_true", help="List available voices and exit")
    parser.add_argument(
        "--system-prompt",
        help="Replace the default prompt (a voice assistant matching the voice's gender, replying in the user's language)",
    )
    parser.add_argument("--debug", action="store_true", help="Print Strands debug logging")
    return parser


async def main(args):
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    if args.debug:
        print(f"[debug] profile={session.profile_name} region={args.region} model={args.model_id} voice={args.voice}")

    model = BedrockNovaSonicModel(
        boto_session=session,
        model_id=args.model_id,
        voice=args.voice,
        audio={"output": {"sample_rate": OUTPUT_SAMPLE_RATE}},
        params={"inferenceConfiguration": {"maxTokens": 1024, "topP": 0.9, "temperature": 0.7}},
    )
    agent = BidiAgent(
        model=model,
        tools=[get_date_and_time, calculate],
        system_prompt=args.system_prompt or default_system_prompt(args.voice) + TOOL_PROMPT_SUFFIX,
    )
    # AudioIO plays the voice and clears playback on barge-in; its console shows the transcripts.
    audio = AudioIO(console=TranscriptConsole())

    print("Talk to the assistant through your microphone. You can interrupt it at any time.")
    print("It can tell you the date and time and do exact calculations.")
    print("Wear headphones so it doesn't hear itself. Press Ctrl+C to quit.")
    print()
    # run() closes the microphone, speaker and stream on exit, including on Ctrl+C.
    await agent.run(inputs=[audio.input()], outputs=[audio.output()])


if __name__ == "__main__":
    args = build_arg_parser().parse_args()
    if args.list_voices:
        for voice_id, description in VOICES.items():
            print(f"{voice_id:<10} {description}")
        sys.exit()
    configure_logging(args.debug)
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass
    print("Session ended.")
