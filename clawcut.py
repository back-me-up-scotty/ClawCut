#!/usr/bin/env python3
"""
ClawCut - Universal LLM Bridge & Proxy (BETA) - v. 4.10.24
-------------------------------------------------------------------------------
LICENSE: ClawCut Personal & Non-Commercial License
Copyright (c) 2026 Niels Gerhardt
https://github.com/back-me-up-scotty/ClawCut

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files, to use, copy, modify, 
and share the software for PERSONAL and NON-COMMERCIAL purposes only, 
subject to the following conditions:

1. The above copyright notice and this permission notice shall be included in 
   all copies or substantial portions of the software.
2. COMMERCIAL USE, sale, or distribution for profit is STRICTLY PROHIBITED.
3. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.

There is no guarantee that ClawCut will work in every 
configuration or with future OpenClaw updates. It is very likely that 
this is not the case. ClawCut therefore depends on the community for 
its development. Make it your own. Share your results.
--------------------------------------------------------------------------------

ClawCut is an experimental project to manipulate, inject JSON-Calls and 
extract JSON clutter from OpenClaw. Why all this? Because smaller models tend to get 
confused by all the JSON clutter and, in particular, can no longer handle 
tool calls — if they are even capable of doing so in the first place.

Features like 'SMART_AMNESIA' and 'PROMPT_TRIMMING' are designed to keep small 
models stable by cutting out "noise". This means the model MAY LOSE awareness 
of previous parts of the conversation. If you need a model to remember 
long-term details within a single session, these features might interfere.

PURPOSE OF THIS PROXY:
OpenClaw is a powerful framework that, by default, sends massive system prompts 
(often >28,000 characters) and complex tool definitions (JSON tools) to the LLM. 
While large cloud models or high-end local models (14B etc.) handle this well, 
small models (7B, 8B) running on limited hardware (Mac/MLX or Raspberry Pi) 
often suffer from "Cognitive Overload":
- Extreme processing latency (slow Time To First Token).
- Forgetting their identity or available tools.
- Hallucinating text answers instead of executing local scripts.
- Connection timeouts or malformed JSON responses.
- Huge RAM consumption

This proxy acts as a "Man-in-the-Middle" between OpenClaw and your local LLM 
server to optimize the data flow:

1. PROMPT TRIMMING: Automatically removes unused default skills from the system 
   prompt to keep the context window small and focused.
2. SMART AMNESIA: Intelligently truncates chat history after successful tool 
   executions to free up "mental space" for the model.
3. ATTENTION FORCER: Injects a reminder at the very end of the user query to 
   ensure the model prioritizes tool usage.
4. TOOL FORCER: Injects keywords for tool calling and points to commands.
5. INPUT RESCUE: Short-circuits known incoming requests (like Cron-Jobs) to 
   bypass LLM latency and ensure 100% reliability for automated tasks.
6. BASH-RESCUE: Detects poorly formatted script calls (e.g., naked code blocks) 
   and converts them into valid OpenClaw tool calls on the fly.

BENEFITS:
- Significantly faster response times (TTFT), as the model has much less text 
  to process upfront.
- Improved reliability when using and calling scripts (bash or whatever).
- Robust error handling for stream interruptions or formatting errors.

WHEN TO USE:
- Ideal for small models (7B-8B) running on hardware like Mac (MLX), Windows 
  or Linux.
- If your model "chats" too much instead of executing commands.

WHEN NOT TO USE:
- If you are using highly intelligent, large models (14B+) that can handle 
  complex prompts natively. In this case, the proxy can act purely as a logger 
  and format translator without manipulating the content if 
  PASS_THROUGH_MODE = True.

HOW TO START:
- See README.md on GitHub. https://github.com/back-me-up-scotty/ClawCut

--KNOWN ISSUES -----------------------------------------------------------------
- Since OpenClaw version 2026.3.12 there are issues with the routing of messages 
  triggered by a cron job. ClawCut clearly sees this messages. The issue seems 
  to be on OpenClaw's side. FORCE_CRON_DELIVERY has unfortunately no effect. 
  OpenClaw ignores it.
--------------------------------------------------------------------------------
"""

from flask import Flask, request, Response
import requests
import json
import re
import sys
import os
import ast
import time
import logging
import subprocess
import signal
import copy
from datetime import datetime, timezone

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)

# ==========================================
# CONFIGURATION PROFILES
# ==========================================
# Note for OpenClaw Configuration (e.g., openclaw.json or openclaw.conf):
# When using this proxy, the specific model name you configure in OpenClaw does NOT matter.
# The proxy intercepts the traffic and completely overrides the requested model 
# based on the selected profile below. 
# 192.168.x.x if it's on a remote machine or 127.0.0.1 if ClawCut and 
# OpenClaw running on the same machine


PROFILES = {
    # Pass Through Values:
    # False: Full intervention - Trimming, Smart Amnesia, Attention Forcer, Rescues — all active. Best for small local models (7B–8B).
    # "small": Format translation - No content manipulation. Only translates between OpenAI and Ollama formats. Best for powerful local models (14B+).
    # "compat":	Light passthrough - For finicky cloud endpoints that are nominally OpenAI-compatible but fail due to tool history, schemas, or specific fields.
    # "full": Cloud passthrough - Raw forward to cloud API with stream translation plus proxy-side cleanup/recovery. Best for cloud models.
    # "transparent" - Transparent passthrough - No prompt/content/tool manipulation. Only model override + stream protocol translation remain.

    "LLM1": {
        "ip": "192.168.0.xxx", # No api_key, no base_url → local, uses http://ip:port/v1/chat/completions
        "port": 8090,
        "model_id": "ollama/Qwen2.5-Coder-7B-Instruct-4bit",
        "model_name": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
        "pass_through": False       # Full proxy intervention for small local models
    },
    "LLM2": {
        "ip": "192.168.0.xxx",
        "port": 11434,
        "model_id": "ollama/mistral-nemo",
        "model_name": "mistral-nemo",
        "pass_through": "small"     # Format translation only, no injection/manipulation
    },
    "LLM3": {
        # baseUrl from openclaw.json → becomes the direct LLM target
        "base_url": "https://integrate.api.nvidia.com/v1/chat/completions",
        # apiKey from openclaw.json → used in Authorization header
        "api_key": "nvapi-",
        # model id from openclaw.json models[].id
        "model_id": "moonshotai/kimi-k2.5",
        # model name from openclaw.json models[].name (or same as model_id)
        "model_name": "moonshotai/kimi-k2.5",
        "pass_through": "compat",      # Compatibility pass-through for cloud providers that need sanitized tool/history payloads
        # headers from openclaw.json → empty here, but can hold extras
        "headers": {},
       
    }
}

# Default Profile (if no flag is provided)
SELECTED_PROFILE = "LLM1"

# Load config.json (if available) to override defaults
_config_path = os.path.join(os.path.dirname(__file__), "config.json")
_config_data = {}
try:
    if os.path.exists(_config_path):
        with open(_config_path, "r", encoding="utf-8") as _cfg_file:
            _config_data = json.load(_cfg_file)
except Exception:
    _config_data = {}

if isinstance(_config_data, dict):
    if isinstance(_config_data.get("PROFILES"), dict):
        PROFILES = _config_data["PROFILES"]
    if _config_data.get("SELECTED_PROFILE"):
        SELECTED_PROFILE = _config_data["SELECTED_PROFILE"]

# Parse Profile from command line — dynamically matches any -LLMx flag defined in PROFILES.
# Use the '-LLM1', '-LLM2', '-LLM3' etc. flag when starting the proxy.
# Example: python clawcut-mlx.py -LLM3 -restart
for _arg in sys.argv[1:]:
    if _arg.startswith('-') and _arg[1:] in PROFILES:
        SELECTED_PROFILE = _arg[1:]
        break

if SELECTED_PROFILE not in PROFILES and PROFILES:
    SELECTED_PROFILE = list(PROFILES.keys())[0]

cfg = PROFILES[SELECTED_PROFILE]


# Active Server Config
# If profile defines base_url, use it directly (cloud providers).
# Otherwise build from ip/port (local servers).
if 'base_url' in cfg:
    LLM_SERVER_URL = cfg['base_url']
else:
    LLM_SERVER_URL = f"http://{cfg['ip']}:{cfg['port']}/v1/chat/completions"

OPENCLAW_MODEL_ID = cfg['model_id']
LLM_MODEL_IDENTIFIER = cfg['model_name']

# Build request headers. Local servers need no auth. Cloud providers need Authorization + optional extras.
LLM_REQUEST_HEADERS = {"Content-Type": "application/json"}
_api_key = cfg.get('api_key') or cfg.get('apiKey')
if _api_key:
    LLM_REQUEST_HEADERS["Authorization"] = f"Bearer {_api_key}"
if 'headers' in cfg:
    LLM_REQUEST_HEADERS.update(cfg['headers'])


# Derive pass-through mode from profile.
# False      → full proxy intervention (injection, amnesia, trimming, rescue)
# "small"    → existing PASS_THROUGH_MODE: format translation only, no manipulation
# "full"     → pass-through with proxy-side cleanup/recovery
# "compat"   → pass-through with compatibility sanitization for strict cloud endpoints
# "transparent" → raw pass-through with no prompt/content/tool manipulation
_pass_through_cfg = cfg.get('pass_through', False)
PASS_THROUGH_MODE = (_pass_through_cfg == "small")
FULL_PASS_THROUGH_MODE = (_pass_through_cfg == "full")
COMPAT_PASS_THROUGH_MODE = (_pass_through_cfg == "compat")

# Logging & Storage Config
# DEBUG_MODE = True prints the full JSON payloads to the console (useful for troubleshooting).
# WRITE_TO_LOGFILE saves the terminal output to the specified PATH_TO_LOGFILE.
# DELETE_LOG_SIZE rotates/deletes the log automatically when it reaches this size to prevent disk full issues.
# Linux/Pi: "/home/username/" 
# Mac: "/Users/username/"
# Windows: "C:/Users/username/"
DEBUG_MODE = True
WRITE_TO_LOGFILE = True
PATH_TO_LOGFILE = '/home/user/clawcut.log' # Change to your preferred log path
DELETE_LOG_SIZE = '10 MB'

# --- SMART AMNESIA MODE ---
# Over time, chat histories get too long for small models to process efficiently.
# If enabled, the proxy watches the current turn: when the last message is a tool 
# result (i.e., the model just received the output of an exec call), the proxy 
# truncates all prior chat history. This creates a "fresh start" for the model 
# to formulate its response based on the tool result alone, preventing infinite 
#loops and keeping the context window and RAM small.

# Outside of tool execution turns, normal chat history is preserved up to 
# `CHAT_HISTORY_LIMIT` messages (see below).
ENABLE_SMART_AMNESIA = True

# Amnesia only when the current turn processes a tool result (last message is ‘tool’)
# Example: You exchanged 5 messages without calling a tool. The context is preserved. 
# Then you call a tool (exec). Result: The context is cut off, and you can no longer 
# retrieve the conversation that took place before the tool was called. 
#
# CHAT_HISTORY_LIMIT lets you specify how much chat history (recent messages in a normal chat) 
# should be retained.
CHAT_HISTORY_LIMIT = 10  # Number of messages (excluding system messages) in chat mode

# --- UNIVERSAL AUTO-DELIVERY ---
# Legacy Support: Before OpenClaw 3.12, the proxy had to manually force the LLM 
# to send its text answers to WhatsApp using the 'message' tool.
# For OpenClaw 3.12+, keep this FALSE. OpenClaw now has "native reply routing" 
# and will automatically route text answers back to the chat interface.
# Setting this to True on modern OpenClaw versions will cause a "Message failed" conflict.
FORCE_AUTO_DELIVERY = False

# Automatically force text delivery to WhatsApp if the request originated from a Cron job.
# Cron jobs lack a native chat interface, so OpenClaw's native routing won't show the text anywhere.
FORCE_CRON_DELIVERY = False
AUTO_DELIVERY_CHANNEL = "whatsapp"  
AUTO_DELIVERY_TARGET = "+123456789" 


# ==========================================
# --- PROXY BEHAVIOR ---
# ==========================================


# BASE PATH FOR SCRIPT RESCUE
# Change this to match the root directory where your scripts (if you have some) are stored, that OpenClaw should execute.
# This matches what you tell the LLM for example in your TOOLS.md. See also EMERGENCY_RESCUES.
# Linux/Pi: "/home/username/" 
# Mac: "/Users/username/"
# Windows: "C:/Users/username/"
EXPECTED_SCRIPT_BASE_PATH = "/home/user/"

# File extensions that should not be treated as safe direct-read text files.
# These files often need a dedicated tool, skill, or extraction command instead of raw `read`.
# They may still be written, edited, or removed through normal tools or skills.
CRITICAL_DIRECT_READ_EXTENSIONS = [".pdf"]

# Default message sent to the user when an audio file is delivered
AUDIO_DELIVERY_MESSAGE = "Here is your audio."

# 1. System Prompt Trimming (Cognitive Overload Protection)
# If True, the proxy aggressively strips out the skills listed in TRIM_SKILLS before sending 
# the prompt to the model, freeing up its attention span for your custom tools.
ENABLE_PROMPT_TRIMMING = True
TRIM_SKILLS = [
    "clawhub", "gemini", "gh-issues", "github", "healthcheck", 
    "nano-pdf", "openai-whisper", "skill-creator", "summarize", 
    "video-frames", "wacli", "weather"
]

# 2. Attention Forcer (End-of-Prompt Injection)
# If True, this injects a strong reminder at the very end of the user's latest message.
ENABLE_ATTENTION_FORCER = True
ATTENTION_FORCER_TEXT = "\n\n[SYSTEM REMINDER: NEVER respond directly with text to requests regarding local scripts, data, or services! You MUST use the ‘exec’ tool FIRST!]"

# 3. Emergency Rescue (Catch & Convert) - Where the tool call magic happens
# Intercepts specific model texts and converts them into hidden 'exec' tool calls.
# Useful if the model only describes what it wants to do, but forgets to output the actual JSON tool call.
# If ENABLE_INPUT_RESCUE is True, this also triggers for incoming user requests (e.g. Cron jobs).
#
# Keep this list empty in the generic source file. User-specific commands belong in config.json
# or in the GUI configuration so the published proxy does not ship with hardcoded local paths.
#
# ENABLE_INPUT_RESCUE takes precedence over the LLM—it scans the incoming user message and bypasses the 
# LLM entirely, going straight to the exec call without even consulting the LLM.
# ENABLE_EMERGENCY_RESCUE intervenes after the LLM—it scans the LLM’s text response in `generate()` 
# and converts recognized keywords into an `exec` call if the model forgot to use the tool.

ENABLE_EMERGENCY_RESCUE = True
ENABLE_INPUT_RESCUE = False
EMERGENCY_RESCUES = [
    {
        "keywords": ["weather", "check"], 
        "command": 'bash /home/user/weather.sh "Frankfurt"'
    },
    {
        "keywords": ["diesel", "price"], 
        "command": 'bash /home/user/.openclaw/workspace/skills/diesel-price/diesel_price.sh'
    },
     {
        "keywords": ["backup", "make"], 
        "command": 'bash /home/user/.openclaw/workspace/skills/system_control/run_bmus.sh'
    }
]# ==========================================

if isinstance(_config_data, dict):
    if "DEBUG_MODE" in _config_data: DEBUG_MODE = _config_data["DEBUG_MODE"]
    if "WRITE_TO_LOGFILE" in _config_data: WRITE_TO_LOGFILE = _config_data["WRITE_TO_LOGFILE"]
    if "PATH_TO_LOGFILE" in _config_data: PATH_TO_LOGFILE = _config_data["PATH_TO_LOGFILE"]
    if "DELETE_LOG_SIZE" in _config_data: DELETE_LOG_SIZE = _config_data["DELETE_LOG_SIZE"]
    if "ENABLE_SMART_AMNESIA" in _config_data: ENABLE_SMART_AMNESIA = _config_data["ENABLE_SMART_AMNESIA"]
    if "CHAT_HISTORY_LIMIT" in _config_data: CHAT_HISTORY_LIMIT = _config_data["CHAT_HISTORY_LIMIT"]
    if "FORCE_AUTO_DELIVERY" in _config_data: FORCE_AUTO_DELIVERY = _config_data["FORCE_AUTO_DELIVERY"]
    if "FORCE_CRON_DELIVERY" in _config_data: FORCE_CRON_DELIVERY = _config_data["FORCE_CRON_DELIVERY"]
    if "AUTO_DELIVERY_CHANNEL" in _config_data: AUTO_DELIVERY_CHANNEL = _config_data["AUTO_DELIVERY_CHANNEL"]
    if "AUTO_DELIVERY_TARGET" in _config_data: AUTO_DELIVERY_TARGET = _config_data["AUTO_DELIVERY_TARGET"]
    if "EXPECTED_SCRIPT_BASE_PATH" in _config_data: EXPECTED_SCRIPT_BASE_PATH = _config_data["EXPECTED_SCRIPT_BASE_PATH"]
    if "CRITICAL_DIRECT_READ_EXTENSIONS" in _config_data: CRITICAL_DIRECT_READ_EXTENSIONS = _config_data["CRITICAL_DIRECT_READ_EXTENSIONS"]
    if "AUDIO_DELIVERY_MESSAGE" in _config_data: AUDIO_DELIVERY_MESSAGE = _config_data["AUDIO_DELIVERY_MESSAGE"]
    if "ENABLE_PROMPT_TRIMMING" in _config_data: ENABLE_PROMPT_TRIMMING = _config_data["ENABLE_PROMPT_TRIMMING"]
    if "TRIM_SKILLS" in _config_data: TRIM_SKILLS = _config_data["TRIM_SKILLS"]
    if "ENABLE_ATTENTION_FORCER" in _config_data: ENABLE_ATTENTION_FORCER = _config_data["ENABLE_ATTENTION_FORCER"]
    if "ATTENTION_FORCER_TEXT" in _config_data: ATTENTION_FORCER_TEXT = _config_data["ATTENTION_FORCER_TEXT"]
    if "ENABLE_EMERGENCY_RESCUE" in _config_data: ENABLE_EMERGENCY_RESCUE = _config_data["ENABLE_EMERGENCY_RESCUE"]
    if "ENABLE_INPUT_RESCUE" in _config_data: ENABLE_INPUT_RESCUE = _config_data["ENABLE_INPUT_RESCUE"]
    if "EMERGENCY_RESCUES" in _config_data: EMERGENCY_RESCUES = _config_data["EMERGENCY_RESCUES"]

try:
    if not os.path.exists(_config_path):
        with open(_config_path, "w", encoding="utf-8") as _cfg_out:
            json.dump({
                "PROFILES": PROFILES,
                "SELECTED_PROFILE": SELECTED_PROFILE,
                "DEBUG_MODE": DEBUG_MODE,
                "WRITE_TO_LOGFILE": WRITE_TO_LOGFILE,
                "PATH_TO_LOGFILE": PATH_TO_LOGFILE,
                "DELETE_LOG_SIZE": DELETE_LOG_SIZE,
                "ENABLE_SMART_AMNESIA": ENABLE_SMART_AMNESIA,
                "CHAT_HISTORY_LIMIT": CHAT_HISTORY_LIMIT,
                "FORCE_AUTO_DELIVERY": FORCE_AUTO_DELIVERY,
                "FORCE_CRON_DELIVERY": FORCE_CRON_DELIVERY,
                "AUTO_DELIVERY_CHANNEL": AUTO_DELIVERY_CHANNEL,
                "AUTO_DELIVERY_TARGET": AUTO_DELIVERY_TARGET,
                "EXPECTED_SCRIPT_BASE_PATH": EXPECTED_SCRIPT_BASE_PATH,
                "CRITICAL_DIRECT_READ_EXTENSIONS": CRITICAL_DIRECT_READ_EXTENSIONS,
                "AUDIO_DELIVERY_MESSAGE": AUDIO_DELIVERY_MESSAGE,
                "ENABLE_PROMPT_TRIMMING": ENABLE_PROMPT_TRIMMING,
                "TRIM_SKILLS": TRIM_SKILLS,
                "ENABLE_ATTENTION_FORCER": ENABLE_ATTENTION_FORCER,
                "ATTENTION_FORCER_TEXT": ATTENTION_FORCER_TEXT,
                "ENABLE_EMERGENCY_RESCUE": ENABLE_EMERGENCY_RESCUE,
                "ENABLE_INPUT_RESCUE": ENABLE_INPUT_RESCUE,
                "EMERGENCY_RESCUES": EMERGENCY_RESCUES
            }, _cfg_out, indent=2, ensure_ascii=False)
except Exception:
    pass

def _parse_size_string(size_str):
    size_str = size_str.strip().upper()
    try:
        if size_str.endswith('MB'): return int(float(size_str.replace('MB', '').strip()) * 1024 * 1024)
        elif size_str.endswith('KB'): return int(float(size_str.replace('KB', '').strip()) * 1024)
        elif size_str.endswith('GB'): return int(float(size_str.replace('GB', '').strip()) * 1024 * 1024 * 1024)
        elif size_str.endswith('B'): return int(float(size_str.replace('B', '').strip()))
        else: return int(size_str)
    except ValueError:
        return 10 * 1024 * 1024

class DualLogger:
    def __init__(self, filepath, max_size_str):
        self.terminal = sys.stdout
        self.filepath = filepath
        self.max_bytes = _parse_size_string(max_size_str)
        self.last_file_message = None

    def _check_size_and_rotate(self):
        if os.path.exists(self.filepath) and os.path.getsize(self.filepath) >= self.max_bytes:
            try: os.remove(self.filepath)
            except OSError: pass

    def write(self, message):
        self.terminal.write(message)
        if WRITE_TO_LOGFILE:
            terminal_name = getattr(self.terminal, "name", "")
            if isinstance(terminal_name, str):
                try:
                    if os.path.realpath(terminal_name) == os.path.realpath(self.filepath):
                        return
                except Exception:
                    pass
            try:
                if hasattr(self.terminal, "isatty") and not self.terminal.isatty():
                    return
            except Exception:
                pass
            if isinstance(message, str) and message.strip() and message == self.last_file_message:
                return
            self._check_size_and_rotate()
            try:
                with open(self.filepath, "a", encoding="utf-8") as log_file:
                    log_file.write(message)
                self.last_file_message = message if isinstance(message, str) and message.strip() else None
            except IOError: pass

    def flush(self):
        self.terminal.flush()

if WRITE_TO_LOGFILE:
    sys.stdout = DualLogger(PATH_TO_LOGFILE, DELETE_LOG_SIZE)
    sys.stderr = sys.stdout

def kill_other_instances():
    current_pid = os.getpid()
    script_name = os.path.basename(__file__)
    try:
        pids = subprocess.check_output(['pgrep', '-f', script_name]).decode().split()
        killed_any = False
        for pid_str in pids:
            try:
                pid = int(pid_str)
                if pid != current_pid:
                    os.kill(pid, signal.SIGTERM)
                    print(f"[SYSTEM] Old background process (PID {pid}) terminated.")
                    killed_any = True
            except Exception:
                pass
        if killed_any:
            time.sleep(2) 
    except subprocess.CalledProcessError:
        pass

def extract_hallucinated_tools(text):
    jsons = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, char in enumerate(text):
        if char == '"' and not escape: in_string = not in_string
        elif char == '\\' and not escape: escape = True
        else: escape = False

        if not in_string:
            if char == '{':
                if depth == 0: start = i
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0 and start != -1:
                    try:
                        obj = json.loads(text[start:i+1])
                        prefix_slice = text[max(0, start-240):start]
                        prefix_match = re.search(
                            r'((?:<\|tool_calls_section_begin\|>\s*)?(?:<\|tool_call_begin\|>\s*)?functions\.([a-zA-Z_][a-zA-Z0-9_]*)\s*(?::\d+)?\s*(?:<\|tool_call_argument_begin\|>\s*)?)$',
                            prefix_slice
                        )
                        if isinstance(obj, dict) and "name" in obj and "arguments" in obj:
                            jsons.append((obj, start, i+1))
                        elif isinstance(obj, dict) and prefix_match:
                            prefix_text = prefix_match.group(1)
                            suffix_slice = text[i+1:i+120]
                            suffix_match = re.match(r'(\s*(?:<\|tool_call_end\|>\s*)?(?:<\|tool_calls_section_end\|>\s*)?)', suffix_slice)
                            end_pos = i+1 + (len(suffix_match.group(1)) if suffix_match else 0)
                            jsons.append((
                                {"name": prefix_match.group(2), "arguments": obj},
                                start - len(prefix_text),
                                end_pos
                            ))
                    except Exception: pass
    pseudo_call_re = re.compile(r'(?s)(```[a-zA-Z]*\s*)?((?:read|write|edit|process|exec))\((.*?)\)(\s*```)?')
    for match in pseudo_call_re.finditer(text):
        call_name = match.group(2)
        args_src = match.group(3).strip()
        if not args_src:
            continue
        try:
            parsed = ast.parse(f"f({args_src})", mode='eval')
            call = parsed.body
            if not isinstance(call, ast.Call):
                continue
            kwargs = {}
            for kw in call.keywords:
                if kw.arg is None:
                    continue
                value = kw.value
                if isinstance(value, ast.Constant):
                    kwargs[kw.arg] = value.value
                elif isinstance(value, ast.Name):
                    kwargs[kw.arg] = value.id
                else:
                    kwargs[kw.arg] = ast.literal_eval(value)
            tool_name = call_name
            if call_name == "exec" and kwargs.get("action") in ("read", "write", "edit", "process"):
                tool_name = kwargs.pop("action")
            if tool_name in ("read", "write", "edit") and "path" in kwargs and "file_path" not in kwargs:
                kwargs["file_path"] = kwargs["path"]
            if tool_name != "exec" or "command" in kwargs:
                jsons.append(({"name": tool_name, "arguments": kwargs}, match.start(), match.end()))
        except Exception:
            pass
    return jsons

def build_short_circuit_response(requested_model, tool_name, arguments):
    def short_circuit_stream():
        msg_obj = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": tool_name, "arguments": arguments}}]
        }
        yield json.dumps({
            "model": requested_model,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "message": msg_obj,
            "done": False
        }).encode('utf-8') + b'\n'
        yield json.dumps({
            "model": requested_model,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "message": {"role": "assistant", "content": ""},
            "done": True
        }).encode('utf-8') + b'\n'
    return Response(short_circuit_stream(), content_type='application/x-ndjson')

def has_critical_direct_read_extension(file_path):
    if not isinstance(file_path, str):
        return False
    path_lower = file_path.lower()
    for ext in CRITICAL_DIRECT_READ_EXTENSIONS:
        if isinstance(ext, str) and ext and path_lower.endswith(ext.lower()):
            return True
    return False

def mentions_critical_extension(text):
    if not isinstance(text, str):
        return False
    text_lower = text.lower()
    for ext in CRITICAL_DIRECT_READ_EXTENSIONS:
        if isinstance(ext, str) and ext:
            ext_lower = ext.lower()
            if ext_lower in text_lower or ext_lower.lstrip('.') in text_lower:
                return True
    return False

def looks_like_binary_tool_result(content):
    if not isinstance(content, str) or not content:
        return False
    if content.startswith('%PDF-'):
        return True
    sample = content[:4000]
    control_chars = sum(1 for ch in sample if ord(ch) < 32 and ch not in '\n\r\t')
    replacement_chars = sample.count('\ufffd') + sample.count('�')
    return control_chars > 8 or replacement_chars > 8

def rewrite_pdf_read_tool_call(tool_name, arguments):
    parsed_args = arguments
    try:
        if isinstance(parsed_args, str):
            parsed_args = json.loads(parsed_args)
    except Exception:
        pass

    if tool_name == "read" and isinstance(parsed_args, dict):
        pdf_path = parsed_args.get("file_path") or parsed_args.get("path") or parsed_args.get("file")
        if isinstance(pdf_path, str) and pdf_path.lower().endswith('.pdf') and has_critical_direct_read_extension(pdf_path):
            safe_path = pdf_path.replace('"', '\\"')
            return "exec", {"command": f'pdftotext "{safe_path}" - 2>/dev/null | head -100'}

    return tool_name, arguments

def sanitize_binary_tool_results(messages):
    sanitized = []
    previous_assistant = None

    for m in copy.deepcopy(messages or []):
        if m.get('role') == 'tool' and isinstance(m.get('content'), str):
            pdf_path = None
            if previous_assistant and previous_assistant.get('tool_calls'):
                for tc in previous_assistant.get('tool_calls', []):
                    if tc.get('function', {}).get('name') != 'read':
                        continue
                    args = tc.get('function', {}).get('arguments')
                    try:
                        args = json.loads(args) if isinstance(args, str) else args
                    except Exception:
                        pass
                    if isinstance(args, dict):
                        candidate = args.get("file_path") or args.get("path") or args.get("file")
                        if has_critical_direct_read_extension(candidate):
                            pdf_path = candidate
                            break
            if pdf_path and looks_like_binary_tool_result(m.get('content', '')):
                m['content'] = f"Binary file content omitted. The previous read tool call targeted a file with a critical direct-read extension: {pdf_path}. The read tool returned raw bytes or unreadable content instead of extracted text. Use a more appropriate tool, skill, or exec-based extraction path before summarizing or editing this file."

        sanitized.append(m)
        if m.get('role') == 'assistant':
            previous_assistant = m

    return sanitized

def extract_running_exec_session_id(content):
    if not isinstance(content, str):
        return None
    match = re.search(r'Command still running \(session ([^,\)]+), pid \d+\)\. Use process', content)
    return match.group(1) if match else None

def extract_missing_exec_script_path(command):
    if not isinstance(command, str):
        return None
    match = re.match(r'\s*(?:bash|sh)\s+"?([^"\s]+)"?(?:\s|$)', command)
    if not match:
        match = re.match(r'\s*"?(\/[^"\s]+\.sh)"?(?:\s|$)', command)
    if not match:
        return None
    script_path = match.group(1)
    if not isinstance(script_path, str) or not script_path.startswith('/'):
        return None
    if EXPECTED_SCRIPT_BASE_PATH and isinstance(EXPECTED_SCRIPT_BASE_PATH, str) and not script_path.startswith(EXPECTED_SCRIPT_BASE_PATH):
        return None
    if not os.path.exists(script_path):
        return script_path
    return None

def clean_cloud_passthrough_messages(messages):
    cleaned = []
    had_tool_protocol = False
    latest_tool_content = None

    for m in copy.deepcopy(messages or []):
        role = m.get('role')
        content = m.get('content')

        # Cloud/OpenAI-compatible backends often choke on historical tool turns.
        # Keep the natural-language history, strip the tool protocol.
        if role == 'tool':
            had_tool_protocol = True
            if isinstance(content, dict):
                content = json.dumps(content)
            if content not in (None, '', []):
                latest_tool_content = content
            continue

        if role == 'assistant':
            if m.get('tool_calls'):
                had_tool_protocol = True
            m.pop('tool_calls', None)
            if content is None:
                m['content'] = ''
                content = ''
            if content in ('', []):
                continue

        cleaned.append(m)

    merged = []
    for m in cleaned:
        if merged and merged[-1].get('role') == 'user' and m.get('role') == 'user':
            prev_content = merged[-1].get('content', '')
            new_content = m.get('content', '')
            if new_content and new_content != prev_content:
                merged[-1] = dict(merged[-1])
                merged[-1]['content'] = prev_content + '\n\n' + new_content
        else:
            merged.append(m)

    if latest_tool_content:
        if merged and merged[-1].get('role') == 'user':
            merged[-1] = dict(merged[-1])
            prev_content = merged[-1].get('content', '')
            merged[-1]['content'] = prev_content + '\n\nTool result:\n' + latest_tool_content if prev_content else 'Tool result:\n' + latest_tool_content
        else:
            merged.append({"role": "user", "content": 'Tool result:\n' + latest_tool_content})

    return merged, had_tool_protocol

def sanitize_tool_schema(obj):
    if isinstance(obj, dict):
        obj.pop('patternProperties', None)
        if obj.get('additionalProperties') is True:
            del obj['additionalProperties']
        if obj.get('properties') == {}:
            del obj['properties']
        for v in obj.values():
            sanitize_tool_schema(v)
    elif isinstance(obj, list):
        for item in obj:
            sanitize_tool_schema(item)

@app.route('/', methods=['GET'])
@app.route('/api/config', methods=['GET', 'POST'])
@app.route('/api/restart', methods=['POST'])
@app.route('/api/logs', methods=['GET'])
@app.route('/api/logs/reset', methods=['POST'])
@app.route('/api/chat', methods=['POST'])
@app.route('/v1/api/chat', methods=['POST'])
def proxy():
    global PROFILES, SELECTED_PROFILE, DEBUG_MODE, WRITE_TO_LOGFILE, PATH_TO_LOGFILE, DELETE_LOG_SIZE
    global ENABLE_SMART_AMNESIA, CHAT_HISTORY_LIMIT, FORCE_AUTO_DELIVERY, FORCE_CRON_DELIVERY
    global AUTO_DELIVERY_CHANNEL, AUTO_DELIVERY_TARGET, EXPECTED_SCRIPT_BASE_PATH, CRITICAL_DIRECT_READ_EXTENSIONS, AUDIO_DELIVERY_MESSAGE
    global ENABLE_PROMPT_TRIMMING, TRIM_SKILLS, ENABLE_ATTENTION_FORCER, ATTENTION_FORCER_TEXT
    global ENABLE_EMERGENCY_RESCUE, ENABLE_INPUT_RESCUE, EMERGENCY_RESCUES

    if request.method == 'GET' and request.path == '/':
        html = """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>ClawCut Web GUI</title>
  <style>
    @import url("https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Fraunces:wght@600&display=swap");
    :root {
      --ink: #13231f;
      --muted: #43605a;
      --bg1: #f2efe6;
      --bg2: #e7f0ef;
      --card: #ffffff;
      --line: #d7e1de;
      --accent: #0f7d6d;
      --accent-2: #f08a5d;
      --shadow: 0 10px 30px rgba(15, 32, 29, 0.12);
    }
    body.dark {
      --ink: #e7efe9;
      --muted: #9bb2ad;
      --bg1: #0b1b18;
      --bg2: #112622;
      --card: #12221f;
      --line: #234039;
      --accent: #2aa690;
      --accent-2: #e38b5b;
      --shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Space Grotesk", sans-serif;
      color: var(--ink);
      background: radial-gradient(1200px 500px at 10% -10%, #f9d6b9 0%, transparent 60%),
                  radial-gradient(1000px 600px at 110% 10%, #bfe8df 0%, transparent 55%),
                  linear-gradient(160deg, var(--bg1), var(--bg2));
      min-height: 100vh;
    }
    body.dark {
      background: radial-gradient(1200px 500px at 10% -10%, #1f3c35 0%, transparent 60%),
                  radial-gradient(1000px 600px at 110% 10%, #2f4b45 0%, transparent 55%),
                  linear-gradient(160deg, var(--bg1), var(--bg2));
    }
    .shell {
      max-width: 1200px;
      margin: 0 auto;
      padding: 32px 20px 60px;
    }
    header {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 16px;
      align-items: center;
      margin-bottom: 24px;
      animation: fadeUp 0.6s ease both;
    }
    .brand {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .brand .title {
      font-family: "Fraunces", serif;
      font-size: 34px;
      letter-spacing: 0.5px;
    }
    .brand .subtitle {
      color: var(--muted);
      font-size: 14px;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      justify-content: flex-end;
    }
    .checkbox-inline {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      color: var(--muted);
      padding: 6px 10px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fbfaf6;
    }
    body.dark .checkbox-inline {
      background: #0f201d;
    }
    button {
      border: none;
      padding: 10px 16px;
      border-radius: 10px;
      font-weight: 600;
      cursor: pointer;
      transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    button.primary {
      background: var(--accent);
      color: #fff;
      box-shadow: var(--shadow);
    }
    button.secondary {
      background: #f6f3eb;
      color: var(--ink);
      border: 1px solid var(--line);
    }
    body.dark button.secondary {
      background: #0f201d;
      color: var(--ink);
    }
    button.warn {
      background: var(--accent-2);
      color: #fff;
      box-shadow: var(--shadow);
    }
    button:active { transform: scale(0.98); }
    main {
      display: grid;
      gap: 18px;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
      box-shadow: var(--shadow);
      animation: fadeUp 0.7s ease both;
    }
    .card h2 {
      margin: 0 0 12px;
      font-size: 18px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .field label {
      font-size: 12px;
      color: var(--muted);
      letter-spacing: 0.3px;
    }
    input[type="text"], input[type="number"], textarea, select {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px 12px;
      font-family: inherit;
      font-size: 14px;
      background: #fff;
      color: var(--ink);
    }
    body.dark input[type="text"], body.dark input[type="number"], body.dark textarea, body.dark select {
      background: #0f201d;
      color: var(--ink);
      border-color: var(--line);
    }
    textarea { min-height: 90px; resize: vertical; }
    .toggle {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fbfaf6;
    }
    body.dark .toggle {
      background: #0f201d;
    }
    .toggle.disabled, .field.disabled {
      opacity: 0.55;
    }
    .profile-list, .rescue-list {
      display: grid;
      gap: 12px;
    }
    .log-viewer {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #0f1d1a;
      color: #e6f1ee;
      padding: 12px;
      white-space: pre-wrap;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      line-height: 1.2;
      height: calc(50 * 1.2em);
      overflow-y: auto;
      overflow-x: hidden;
    }
    body.dark .log-viewer {
      background: #0b1412;
      color: #d9e7e2;
    }
    .profile-card, .rescue-card {
      border: 1px dashed #c7d3cf;
      border-radius: 14px;
      padding: 12px;
      background: #fcfbf8;
    }
    body.dark .profile-card, body.dark .rescue-card {
      background: #12221f;
      border-color: #234039;
    }
    .row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }
    .row .spacer { flex: 1 1 auto; }
    .hint {
      color: var(--muted);
      font-size: 12px;
      margin-top: 6px;
    }
    #toast {
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: #0f7d6d;
      color: #fff;
      padding: 12px 16px;
      border-radius: 12px;
      opacity: 0;
      transform: translateY(10px);
      transition: opacity 0.3s ease, transform 0.3s ease;
      pointer-events: none;
      box-shadow: var(--shadow);
    }
    #toast.show {
      opacity: 1;
      transform: translateY(0);
    }
    @keyframes fadeUp {
      from { opacity: 0; transform: translateY(10px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @media (max-width: 720px) {
      header { grid-template-columns: 1fr; }
      .actions { justify-content: flex-start; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div class="brand">
        <div class="title">ClawCut Control</div>
        <div class="subtitle">Web GUI for profiles, proxy settings, and restart</div>
      </div>
      <div class="actions">
        <label class="checkbox-inline"><input type="checkbox" id="EMPTY_LOGS_ON_RELOAD"/> Empty logs on Restart</label>
        <button class="secondary" id="reloadBtn">Stop Server</button>
        <button class="warn" id="restartBtn">Restart With Profile</button>
        <button class="primary" id="saveBtn">Save Config</button>        
        <button class="secondary" id="resetLogsBtn">Reset Logfiles</button>
        <button class="secondary" id="themeToggle">Toggle Dark Mode</button>
      </div>
    </header>

    <main>
      <section class="card">
        <h2>Active Profile</h2>
        <div class="field">
          <label for="SELECTED_PROFILE">SELECTED_PROFILE</label>
          <select id="SELECTED_PROFILE"></select>
        </div>
        <div class="hint">Profile changes take effect after restart.</div>
      </section>

      <section class="card">
        <h2>LLM Profiles</h2>
        <div class="profile-list" id="profiles"></div>
        <div class="row" style="margin-top:10px;">
          <button class="secondary" id="addProfile">Add Profile</button>
        </div>
      </section>

      <section class="card">
        <h2>Logging</h2>
        <div class="grid">
          <div class="toggle"><input type="checkbox" id="DEBUG_MODE"/> <label for="DEBUG_MODE">DEBUG_MODE</label></div>
          <div class="toggle"><input type="checkbox" id="WRITE_TO_LOGFILE"/> <label for="WRITE_TO_LOGFILE">WRITE_TO_LOGFILE</label></div>
          <div class="field">
            <label for="PATH_TO_LOGFILE">PATH_TO_LOGFILE</label>
            <input type="text" id="PATH_TO_LOGFILE"/>
          </div>
          <div class="field">
            <label for="DELETE_LOG_SIZE">DELETE_LOG_SIZE</label>
            <input type="text" id="DELETE_LOG_SIZE"/>
          </div>
        </div>
      </section>

      <section class="card">
        <h2>Smart Amnesia</h2>
        <div class="grid">
          <div class="toggle"><input type="checkbox" id="ENABLE_SMART_AMNESIA"/> <label for="ENABLE_SMART_AMNESIA">ENABLE_SMART_AMNESIA</label></div>
          <div class="field">
            <label for="CHAT_HISTORY_LIMIT">CHAT_HISTORY_LIMIT</label>
            <input type="number" id="CHAT_HISTORY_LIMIT" min="0"/>
          </div>
        </div>
      </section>

      <section class="card">
        <h2>Auto Delivery</h2>
        <div class="grid">
          <div class="toggle"><input type="checkbox" id="FORCE_AUTO_DELIVERY"/> <label for="FORCE_AUTO_DELIVERY">FORCE_AUTO_DELIVERY</label></div>
          <div class="toggle"><input type="checkbox" id="FORCE_CRON_DELIVERY"/> <label for="FORCE_CRON_DELIVERY">FORCE_CRON_DELIVERY</label></div>
          <div class="field">
            <label for="AUTO_DELIVERY_CHANNEL">AUTO_DELIVERY_CHANNEL</label>
            <input type="text" id="AUTO_DELIVERY_CHANNEL"/>
          </div>
          <div class="field">
            <label for="AUTO_DELIVERY_TARGET">AUTO_DELIVERY_TARGET</label>
            <input type="text" id="AUTO_DELIVERY_TARGET"/>
          </div>
          <div class="field">
            <label for="AUDIO_DELIVERY_MESSAGE">AUDIO_DELIVERY_MESSAGE</label>
            <input type="text" id="AUDIO_DELIVERY_MESSAGE"/>
          </div>
        </div>
      </section>

      <section class="card">
        <h2>Prompt Trimming</h2>
        <div class="grid">
          <div class="toggle"><input type="checkbox" id="ENABLE_PROMPT_TRIMMING"/> <label for="ENABLE_PROMPT_TRIMMING">ENABLE_PROMPT_TRIMMING</label></div>
          <div class="field">
            <label for="TRIM_SKILLS">TRIM_SKILLS (comma separated)</label>
            <input type="text" id="TRIM_SKILLS"/>
          </div>
        </div>
      </section>

      <section class="card">
        <h2>Attention Forcer</h2>
        <div class="grid">
          <div class="toggle"><input type="checkbox" id="ENABLE_ATTENTION_FORCER"/> <label for="ENABLE_ATTENTION_FORCER">ENABLE_ATTENTION_FORCER</label></div>
          <div class="field">
            <label for="ATTENTION_FORCER_TEXT">ATTENTION_FORCER_TEXT</label>
            <textarea id="ATTENTION_FORCER_TEXT"></textarea>
          </div>
        </div>
      </section>

      <section class="card">
        <h2>Rescue & Scripts</h2>
        <div class="grid">
          <div class="toggle"><input type="checkbox" id="ENABLE_EMERGENCY_RESCUE"/> <label for="ENABLE_EMERGENCY_RESCUE">ENABLE_EMERGENCY_RESCUE</label></div>
          <div class="toggle"><input type="checkbox" id="ENABLE_INPUT_RESCUE"/> <label for="ENABLE_INPUT_RESCUE">ENABLE_INPUT_RESCUE</label></div>
          <div class="field">
            <label for="EXPECTED_SCRIPT_BASE_PATH">EXPECTED_SCRIPT_BASE_PATH</label>
            <input type="text" id="EXPECTED_SCRIPT_BASE_PATH"/>
          </div>
          <div class="field">
            <label for="CRITICAL_DIRECT_READ_EXTENSIONS">CRITICAL_DIRECT_READ_EXTENSIONS (critical formats, comma separated)</label>
            <input type="text" id="CRITICAL_DIRECT_READ_EXTENSIONS"/>
          </div>
        </div>
        <div class="rescue-list" id="rescues" style="margin-top:12px;"></div>
        <div class="row" style="margin-top:10px;">
          <button class="secondary" id="addRescue">Add Rescue</button>
        </div>
        <div class="field" style="margin-top:12px;">
          <label><input type="checkbox" id="AUTO_SCROLL_LOGS" checked/> Autoscroll</label>
          <label>Logfile (last lines)</label>
          <pre class="log-viewer" id="logViewer"></pre>
        </div>
      </section>
    </main>
  </div>

  <div id="toast">Saved</div>

  <script>
    const byId = (id) => document.getElementById(id);
    const profilesWrap = byId("profiles");
    const rescuesWrap = byId("rescues");
    const selectedProfileSelect = byId("SELECTED_PROFILE");
    const logViewer = byId("logViewer");
    const emptyOnReload = byId("EMPTY_LOGS_ON_RELOAD");
    const autoScrollLogs = byId("AUTO_SCROLL_LOGS");

    function showToast(text) {
      const toast = byId("toast");
      toast.textContent = text;
      toast.classList.add("show");
      setTimeout(() => toast.classList.remove("show"), 2200);
    }

    function addProfileCard(name, data) {
      const card = document.createElement("div");
      card.className = "profile-card";
      card.innerHTML = `
        <div class="row">
          <strong>Profile</strong>
          <div class="spacer"></div>
          <button class="secondary remove-profile">Remove</button>
        </div>
        <div class="grid" style="margin-top:10px;">
          <div class="field"><label>Name</label><input type="text" class="profile-name" value="${name || ""}"/></div>
          <div class="field"><label>ip</label><input type="text" class="profile-ip" value="${data.ip || ""}"/></div>
          <div class="field"><label>port</label><input type="number" class="profile-port" value="${data.port || ""}"/></div>
          <div class="field"><label>base_url</label><input type="text" class="profile-base_url" value="${data.base_url || ""}"/></div>
          <div class="field"><label>api_key</label><input type="text" class="profile-api_key" value="${data.api_key || ""}"/></div>
          <div class="field"><label>model_id</label><input type="text" class="profile-model_id" value="${data.model_id || ""}"/></div>
          <div class="field"><label>model_name</label><input type="text" class="profile-model_name" value="${data.model_name || ""}"/></div>
          <div class="field"><label>pass_through</label>
            <select class="profile-pass_through">
              <option value="false">Off (False)</option>
              <option value="small">Small</option>
              <option value="compat">Compat</option>
              <option value="full">Full</option>
              <option value="transparent">Transparent</option>
            </select>
          </div>
          <div class="field" style="grid-column: 1 / -1;"><label>headers (JSON)</label><textarea class="profile-headers">${data.headers ? JSON.stringify(data.headers, null, 2) : ""}</textarea></div>
        </div>
      `;
      const passSelect = card.querySelector(".profile-pass_through");
      const pt = data.pass_through;
      if (pt === "small") passSelect.value = "small";
      else if (pt === "compat") passSelect.value = "compat";
      else if (pt === "full") passSelect.value = "full";
      else if (pt === "transparent") passSelect.value = "transparent";
      else passSelect.value = "false";
      passSelect.addEventListener("change", () => applyModeLocks());
      card.querySelector(".profile-name").addEventListener("input", () => applyModeLocks());
      card.querySelector(".remove-profile").addEventListener("click", () => card.remove());
      profilesWrap.appendChild(card);
    }

    function addRescueCard(data) {
      const card = document.createElement("div");
      card.className = "rescue-card";
      const keywords = Array.isArray(data.keywords) ? data.keywords.join(", ") : "";
      card.innerHTML = `
        <div class="row">
          <strong>Rescue</strong>
          <div class="spacer"></div>
          <button class="secondary remove-rescue">Remove</button>
        </div>
        <div class="grid" style="margin-top:10px;">
          <div class="field"><label>keywords</label><input type="text" class="rescue-keywords" value="${keywords}"/></div>
          <div class="field" style="grid-column: 1 / -1;"><label>command</label><input type="text" class="rescue-command" value="${data.command || ""}"/></div>
        </div>
      `;
      card.querySelector(".remove-rescue").addEventListener("click", () => card.remove());
      rescuesWrap.appendChild(card);
    }

    async function loadLogs() {
      const res = await fetch("/api/logs");
      const out = await res.json();
      if (out && out.log !== undefined) {
        logViewer.textContent = out.log;
        if (autoScrollLogs.checked) {
          logViewer.scrollTop = logViewer.scrollHeight;
        }
      }
    }

    async function resetLogs() {
      const res = await fetch("/api/logs/reset", { method: "POST" });
      const out = await res.json();
      if (!out.ok) throw new Error(out.error || "Reset failed");
      await loadLogs();
      showToast("Logs reset");
    }

    async function loadConfig() {
      const res = await fetch("/api/config");
      const cfg = await res.json();

      selectedProfileSelect.innerHTML = "";
      Object.keys(cfg.PROFILES || {}).forEach((name) => {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        selectedProfileSelect.appendChild(opt);
      });
      selectedProfileSelect.value = cfg.SELECTED_PROFILE || "";

      byId("DEBUG_MODE").checked = !!cfg.DEBUG_MODE;
      byId("WRITE_TO_LOGFILE").checked = !!cfg.WRITE_TO_LOGFILE;
      byId("PATH_TO_LOGFILE").value = cfg.PATH_TO_LOGFILE || "";
      byId("DELETE_LOG_SIZE").value = cfg.DELETE_LOG_SIZE || "";

      byId("ENABLE_SMART_AMNESIA").checked = !!cfg.ENABLE_SMART_AMNESIA;
      byId("CHAT_HISTORY_LIMIT").value = cfg.CHAT_HISTORY_LIMIT ?? 0;

      byId("FORCE_AUTO_DELIVERY").checked = !!cfg.FORCE_AUTO_DELIVERY;
      byId("FORCE_CRON_DELIVERY").checked = !!cfg.FORCE_CRON_DELIVERY;
      byId("AUTO_DELIVERY_CHANNEL").value = cfg.AUTO_DELIVERY_CHANNEL || "";
      byId("AUTO_DELIVERY_TARGET").value = cfg.AUTO_DELIVERY_TARGET || "";
      byId("AUDIO_DELIVERY_MESSAGE").value = cfg.AUDIO_DELIVERY_MESSAGE || "";

      byId("ENABLE_PROMPT_TRIMMING").checked = !!cfg.ENABLE_PROMPT_TRIMMING;
      byId("TRIM_SKILLS").value = Array.isArray(cfg.TRIM_SKILLS) ? cfg.TRIM_SKILLS.join(", ") : "";

      byId("ENABLE_ATTENTION_FORCER").checked = !!cfg.ENABLE_ATTENTION_FORCER;
      byId("ATTENTION_FORCER_TEXT").value = cfg.ATTENTION_FORCER_TEXT || "";

      byId("ENABLE_EMERGENCY_RESCUE").checked = !!cfg.ENABLE_EMERGENCY_RESCUE;
      byId("ENABLE_INPUT_RESCUE").checked = !!cfg.ENABLE_INPUT_RESCUE;
      byId("EXPECTED_SCRIPT_BASE_PATH").value = cfg.EXPECTED_SCRIPT_BASE_PATH || "";
      byId("CRITICAL_DIRECT_READ_EXTENSIONS").value = Array.isArray(cfg.CRITICAL_DIRECT_READ_EXTENSIONS) ? cfg.CRITICAL_DIRECT_READ_EXTENSIONS.join(", ") : "";

      profilesWrap.innerHTML = "";
      Object.entries(cfg.PROFILES || {}).forEach(([name, data]) => addProfileCard(name, data || {}));

      rescuesWrap.innerHTML = "";
      (cfg.EMERGENCY_RESCUES || []).forEach((r) => addRescueCard(r || {}));
      applyModeLocks();
    }

    function setLockedState(id, locked, reason) {
      const el = byId(id);
      if (!el) return;
      el.disabled = locked;
      el.title = locked ? reason : "";
      const wrap = el.closest(".toggle") || el.closest(".field");
      if (wrap) {
        wrap.classList.toggle("disabled", locked);
        wrap.title = locked ? reason : "";
      }
    }

    function getSelectedPassThroughValue() {
      const selectedName = selectedProfileSelect.value;
      let value = "false";
      document.querySelectorAll(".profile-card").forEach((card) => {
        const name = card.querySelector(".profile-name").value.trim();
        if (name === selectedName) {
          value = card.querySelector(".profile-pass_through").value || "false";
        }
      });
      return value;
    }

    function applyModeLocks() {
      const passValue = getSelectedPassThroughValue();
      const locked = passValue !== "false";
      const reason = `Inactive for pass_through=${passValue}. This option currently only works in Off (False).`;
      const transparentLocked = passValue === "transparent";
      const promptTrimmingLocked = passValue === "small" || transparentLocked;
      const promptTrimmingReason = `Inactive for pass_through=${passValue}. This option currently works in Off (False), Full, and Compat.`;
      const transparentReason = `Inactive for pass_through=${passValue}. Transparent mode disables proxy-side manipulation.`;

      setLockedState("ENABLE_SMART_AMNESIA", locked, reason);
      setLockedState("CHAT_HISTORY_LIMIT", locked, reason);
      setLockedState("ENABLE_PROMPT_TRIMMING", promptTrimmingLocked, promptTrimmingReason);
      setLockedState("TRIM_SKILLS", promptTrimmingLocked, promptTrimmingReason);
      setLockedState("ENABLE_ATTENTION_FORCER", locked, reason);
      setLockedState("ATTENTION_FORCER_TEXT", locked, reason);
      setLockedState("ENABLE_EMERGENCY_RESCUE", locked, reason);
      setLockedState("FORCE_AUTO_DELIVERY", transparentLocked, transparentReason);
      setLockedState("FORCE_CRON_DELIVERY", transparentLocked, transparentReason);
      setLockedState("ENABLE_INPUT_RESCUE", transparentLocked, transparentReason);
      setLockedState("EXPECTED_SCRIPT_BASE_PATH", transparentLocked, transparentReason);
      setLockedState("CRITICAL_DIRECT_READ_EXTENSIONS", transparentLocked, transparentReason);
      setLockedState("addRescue", transparentLocked, transparentReason);
      document.querySelectorAll(".rescue-keywords, .rescue-command, .remove-rescue").forEach((el) => {
        el.disabled = transparentLocked;
        el.title = transparentLocked ? transparentReason : "";
      });
    }

    function gatherConfig() {
      const profiles = {};
      document.querySelectorAll(".profile-card").forEach((card) => {
        const name = card.querySelector(".profile-name").value.trim();
        if (!name) return;
        const data = {};
        const ip = card.querySelector(".profile-ip").value.trim();
        if (ip) data.ip = ip;
        const portVal = card.querySelector(".profile-port").value.trim();
        if (portVal !== "") data.port = Number(portVal);
        const baseUrl = card.querySelector(".profile-base_url").value.trim();
        if (baseUrl) data.base_url = baseUrl;
        const apiKey = card.querySelector(".profile-api_key").value.trim();
        if (apiKey) data.api_key = apiKey;
        const modelId = card.querySelector(".profile-model_id").value.trim();
        if (modelId) data.model_id = modelId;
        const modelName = card.querySelector(".profile-model_name").value.trim();
        if (modelName) data.model_name = modelName;
        const passVal = card.querySelector(".profile-pass_through").value;
        data.pass_through = passVal === "false" ? false : passVal;
        const headersVal = card.querySelector(".profile-headers").value.trim();
        if (headersVal) {
          try { data.headers = JSON.parse(headersVal); }
          catch (e) { throw new Error("Invalid headers JSON in profile " + name); }
        } else {
          data.headers = {};
        }
        profiles[name] = data;
      });

      const rescues = [];
      document.querySelectorAll(".rescue-card").forEach((card) => {
        const keywords = card.querySelector(".rescue-keywords").value.split(",").map(k => k.trim()).filter(Boolean);
        const command = card.querySelector(".rescue-command").value.trim();
        if (keywords.length || command) rescues.push({ keywords, command });
      });

      return {
        PROFILES: profiles,
        SELECTED_PROFILE: selectedProfileSelect.value,
        DEBUG_MODE: byId("DEBUG_MODE").checked,
        WRITE_TO_LOGFILE: byId("WRITE_TO_LOGFILE").checked,
        PATH_TO_LOGFILE: byId("PATH_TO_LOGFILE").value,
        DELETE_LOG_SIZE: byId("DELETE_LOG_SIZE").value,
        ENABLE_SMART_AMNESIA: byId("ENABLE_SMART_AMNESIA").checked,
        CHAT_HISTORY_LIMIT: Number(byId("CHAT_HISTORY_LIMIT").value || 0),
        FORCE_AUTO_DELIVERY: byId("FORCE_AUTO_DELIVERY").checked,
        FORCE_CRON_DELIVERY: byId("FORCE_CRON_DELIVERY").checked,
        AUTO_DELIVERY_CHANNEL: byId("AUTO_DELIVERY_CHANNEL").value,
        AUTO_DELIVERY_TARGET: byId("AUTO_DELIVERY_TARGET").value,
        EXPECTED_SCRIPT_BASE_PATH: byId("EXPECTED_SCRIPT_BASE_PATH").value,
        CRITICAL_DIRECT_READ_EXTENSIONS: byId("CRITICAL_DIRECT_READ_EXTENSIONS").value.split(",").map(k => k.trim()).filter(Boolean),
        AUDIO_DELIVERY_MESSAGE: byId("AUDIO_DELIVERY_MESSAGE").value,
        ENABLE_PROMPT_TRIMMING: byId("ENABLE_PROMPT_TRIMMING").checked,
        TRIM_SKILLS: byId("TRIM_SKILLS").value.split(",").map(k => k.trim()).filter(Boolean),
        ENABLE_ATTENTION_FORCER: byId("ENABLE_ATTENTION_FORCER").checked,
        ATTENTION_FORCER_TEXT: byId("ATTENTION_FORCER_TEXT").value,
        ENABLE_EMERGENCY_RESCUE: byId("ENABLE_EMERGENCY_RESCUE").checked,
        ENABLE_INPUT_RESCUE: byId("ENABLE_INPUT_RESCUE").checked,
        EMERGENCY_RESCUES: rescues
      };
    }

    byId("addProfile").addEventListener("click", () => addProfileCard("", {}));
    byId("addRescue").addEventListener("click", () => addRescueCard({ keywords: [], command: "" }));
    selectedProfileSelect.addEventListener("change", () => applyModeLocks());
    byId("reloadBtn").addEventListener("click", async () => {
      try {
        if (emptyOnReload.checked) await resetLogs();
        const res = await fetch("/api/restart", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({})
        });
        const out = await res.json();
        if (!out.ok) throw new Error(out.error || "Stop failed");
        showToast("Stopping...");
      } catch (e) {
        showToast(e.message || "Stop failed");
      }
    });
    byId("resetLogsBtn").addEventListener("click", async () => {
      try { await resetLogs(); }
      catch (e) { showToast(e.message || "Reset failed"); }
    });
    byId("themeToggle").addEventListener("click", () => {
      const next = !document.body.classList.contains("dark");
      document.body.classList.toggle("dark", next);
      localStorage.setItem("clawcut_theme", next ? "dark" : "light");
    });

    byId("saveBtn").addEventListener("click", async () => {
      try {
        const payload = gatherConfig();
        const res = await fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const out = await res.json();
        if (!out.ok) throw new Error(out.error || "Save failed");
        await loadConfig();
        showToast("Config saved");
      } catch (e) {
        showToast(e.message || "Save failed");
      }
    });

    byId("restartBtn").addEventListener("click", async () => {
      try {
        if (emptyOnReload.checked) await resetLogs();
        const res = await fetch("/api/restart", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profile: selectedProfileSelect.value })
        });
        const out = await res.json();
        if (!out.ok) throw new Error(out.error || "Restart failed");
        showToast("Restarting...");
      } catch (e) {
        showToast(e.message || "Restart failed");
      }
    });

    async function loadConfigWithRetry(attempts = 5) {
    for (let i = 0; i < attempts; i++) {
    try {
      await loadConfig();
      return;
    } catch (e) {
      await new Promise(r => setTimeout(r, 500));
    }
  }
}

const savedTheme = localStorage.getItem("clawcut_theme");
if (savedTheme === "dark") document.body.classList.add("dark");
loadConfigWithRetry().then(() => loadLogs());
setInterval(loadLogs, 1000);
  </script>
</body>
</html>"""
        return Response(html, content_type='text/html')

    if request.path == '/api/logs':
        try:
            log_text = ""
            if os.path.exists(PATH_TO_LOGFILE):
                with open(PATH_TO_LOGFILE, "r", encoding="utf-8", errors="replace") as _log_file:
                    log_text = _log_file.read()
            if log_text:
                lines = log_text.splitlines()
                if len(lines) > 500:
                    lines = lines[-500:]
                log_text = "\n".join(lines)
            return Response(json.dumps({"ok": True, "log": log_text}, ensure_ascii=False), content_type='application/json')
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}), 500

    if request.path == '/api/logs/reset':
        try:
            with open(PATH_TO_LOGFILE, "w", encoding="utf-8") as _log_file:
                _log_file.write("")
            return json.dumps({"ok": True}), 200
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}), 500

    if request.path == '/api/config':
        if request.method == 'GET':
            return Response(json.dumps({
                "PROFILES": PROFILES,
                "SELECTED_PROFILE": SELECTED_PROFILE,
                "DEBUG_MODE": DEBUG_MODE,
                "WRITE_TO_LOGFILE": WRITE_TO_LOGFILE,
                "PATH_TO_LOGFILE": PATH_TO_LOGFILE,
                "DELETE_LOG_SIZE": DELETE_LOG_SIZE,
                "ENABLE_SMART_AMNESIA": ENABLE_SMART_AMNESIA,
                "CHAT_HISTORY_LIMIT": CHAT_HISTORY_LIMIT,
                "FORCE_AUTO_DELIVERY": FORCE_AUTO_DELIVERY,
                "FORCE_CRON_DELIVERY": FORCE_CRON_DELIVERY,
                "AUTO_DELIVERY_CHANNEL": AUTO_DELIVERY_CHANNEL,
                "AUTO_DELIVERY_TARGET": AUTO_DELIVERY_TARGET,
                "EXPECTED_SCRIPT_BASE_PATH": EXPECTED_SCRIPT_BASE_PATH,
                "CRITICAL_DIRECT_READ_EXTENSIONS": CRITICAL_DIRECT_READ_EXTENSIONS,
                "AUDIO_DELIVERY_MESSAGE": AUDIO_DELIVERY_MESSAGE,
                "ENABLE_PROMPT_TRIMMING": ENABLE_PROMPT_TRIMMING,
                "TRIM_SKILLS": TRIM_SKILLS,
                "ENABLE_ATTENTION_FORCER": ENABLE_ATTENTION_FORCER,
                "ATTENTION_FORCER_TEXT": ATTENTION_FORCER_TEXT,
                "ENABLE_EMERGENCY_RESCUE": ENABLE_EMERGENCY_RESCUE,
                "ENABLE_INPUT_RESCUE": ENABLE_INPUT_RESCUE,
                "EMERGENCY_RESCUES": EMERGENCY_RESCUES
            }, ensure_ascii=False), content_type='application/json')

        incoming = request.json or {}
        if isinstance(incoming.get("PROFILES"), dict): PROFILES = incoming["PROFILES"]
        if "SELECTED_PROFILE" in incoming: SELECTED_PROFILE = incoming["SELECTED_PROFILE"]
        if "DEBUG_MODE" in incoming: DEBUG_MODE = incoming["DEBUG_MODE"]
        if "WRITE_TO_LOGFILE" in incoming: WRITE_TO_LOGFILE = incoming["WRITE_TO_LOGFILE"]
        if "PATH_TO_LOGFILE" in incoming: PATH_TO_LOGFILE = incoming["PATH_TO_LOGFILE"]
        if "DELETE_LOG_SIZE" in incoming: DELETE_LOG_SIZE = incoming["DELETE_LOG_SIZE"]
        if "ENABLE_SMART_AMNESIA" in incoming: ENABLE_SMART_AMNESIA = incoming["ENABLE_SMART_AMNESIA"]
        if "CHAT_HISTORY_LIMIT" in incoming: CHAT_HISTORY_LIMIT = incoming["CHAT_HISTORY_LIMIT"]
        if "FORCE_AUTO_DELIVERY" in incoming: FORCE_AUTO_DELIVERY = incoming["FORCE_AUTO_DELIVERY"]
        if "FORCE_CRON_DELIVERY" in incoming: FORCE_CRON_DELIVERY = incoming["FORCE_CRON_DELIVERY"]
        if "AUTO_DELIVERY_CHANNEL" in incoming: AUTO_DELIVERY_CHANNEL = incoming["AUTO_DELIVERY_CHANNEL"]
        if "AUTO_DELIVERY_TARGET" in incoming: AUTO_DELIVERY_TARGET = incoming["AUTO_DELIVERY_TARGET"]
        if "EXPECTED_SCRIPT_BASE_PATH" in incoming: EXPECTED_SCRIPT_BASE_PATH = incoming["EXPECTED_SCRIPT_BASE_PATH"]
        if "CRITICAL_DIRECT_READ_EXTENSIONS" in incoming: CRITICAL_DIRECT_READ_EXTENSIONS = incoming["CRITICAL_DIRECT_READ_EXTENSIONS"]
        if "AUDIO_DELIVERY_MESSAGE" in incoming: AUDIO_DELIVERY_MESSAGE = incoming["AUDIO_DELIVERY_MESSAGE"]
        if "ENABLE_PROMPT_TRIMMING" in incoming: ENABLE_PROMPT_TRIMMING = incoming["ENABLE_PROMPT_TRIMMING"]
        if "TRIM_SKILLS" in incoming: TRIM_SKILLS = incoming["TRIM_SKILLS"]
        if "ENABLE_ATTENTION_FORCER" in incoming: ENABLE_ATTENTION_FORCER = incoming["ENABLE_ATTENTION_FORCER"]
        if "ATTENTION_FORCER_TEXT" in incoming: ATTENTION_FORCER_TEXT = incoming["ATTENTION_FORCER_TEXT"]
        if "ENABLE_EMERGENCY_RESCUE" in incoming: ENABLE_EMERGENCY_RESCUE = incoming["ENABLE_EMERGENCY_RESCUE"]
        if "ENABLE_INPUT_RESCUE" in incoming: ENABLE_INPUT_RESCUE = incoming["ENABLE_INPUT_RESCUE"]
        if "EMERGENCY_RESCUES" in incoming: EMERGENCY_RESCUES = incoming["EMERGENCY_RESCUES"]

        if SELECTED_PROFILE not in PROFILES and PROFILES:
            SELECTED_PROFILE = list(PROFILES.keys())[0]

        try:
            with open(_config_path, "w", encoding="utf-8") as _cfg_out:
                json.dump({
                    "PROFILES": PROFILES,
                    "SELECTED_PROFILE": SELECTED_PROFILE,
                    "DEBUG_MODE": DEBUG_MODE,
                    "WRITE_TO_LOGFILE": WRITE_TO_LOGFILE,
                    "PATH_TO_LOGFILE": PATH_TO_LOGFILE,
                    "DELETE_LOG_SIZE": DELETE_LOG_SIZE,
                    "ENABLE_SMART_AMNESIA": ENABLE_SMART_AMNESIA,
                    "CHAT_HISTORY_LIMIT": CHAT_HISTORY_LIMIT,
                    "FORCE_AUTO_DELIVERY": FORCE_AUTO_DELIVERY,
                    "FORCE_CRON_DELIVERY": FORCE_CRON_DELIVERY,
                    "AUTO_DELIVERY_CHANNEL": AUTO_DELIVERY_CHANNEL,
                    "AUTO_DELIVERY_TARGET": AUTO_DELIVERY_TARGET,
                    "EXPECTED_SCRIPT_BASE_PATH": EXPECTED_SCRIPT_BASE_PATH,
                    "CRITICAL_DIRECT_READ_EXTENSIONS": CRITICAL_DIRECT_READ_EXTENSIONS,
                    "AUDIO_DELIVERY_MESSAGE": AUDIO_DELIVERY_MESSAGE,
                    "ENABLE_PROMPT_TRIMMING": ENABLE_PROMPT_TRIMMING,
                    "TRIM_SKILLS": TRIM_SKILLS,
                    "ENABLE_ATTENTION_FORCER": ENABLE_ATTENTION_FORCER,
                    "ATTENTION_FORCER_TEXT": ATTENTION_FORCER_TEXT,
                    "ENABLE_EMERGENCY_RESCUE": ENABLE_EMERGENCY_RESCUE,
                    "ENABLE_INPUT_RESCUE": ENABLE_INPUT_RESCUE,
                    "EMERGENCY_RESCUES": EMERGENCY_RESCUES
                }, _cfg_out, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}), 500

        return json.dumps({"ok": True, "selected_profile": SELECTED_PROFILE}), 200

    if request.path == '/api/restart':
        incoming = request.json or {}
        if "profile" not in incoming:
            try:
                subprocess.Popen([sys.executable, __file__, "-stop"])
            except Exception as e:
                return json.dumps({"ok": False, "error": str(e)}), 500
            return json.dumps({"ok": True, "stopping": True}), 200
        profile = incoming.get("profile") or SELECTED_PROFILE
        if isinstance(profile, str) and profile in PROFILES:
            SELECTED_PROFILE = profile
        if SELECTED_PROFILE not in PROFILES and PROFILES:
            SELECTED_PROFILE = list(PROFILES.keys())[0]
        try:
            with open(_config_path, "w", encoding="utf-8") as _cfg_out:
                json.dump({
                    "PROFILES": PROFILES,
                    "SELECTED_PROFILE": SELECTED_PROFILE,
                    "DEBUG_MODE": DEBUG_MODE,
                    "WRITE_TO_LOGFILE": WRITE_TO_LOGFILE,
                    "PATH_TO_LOGFILE": PATH_TO_LOGFILE,
                    "DELETE_LOG_SIZE": DELETE_LOG_SIZE,
                    "ENABLE_SMART_AMNESIA": ENABLE_SMART_AMNESIA,
                    "CHAT_HISTORY_LIMIT": CHAT_HISTORY_LIMIT,
                    "FORCE_AUTO_DELIVERY": FORCE_AUTO_DELIVERY,
                    "FORCE_CRON_DELIVERY": FORCE_CRON_DELIVERY,
                    "AUTO_DELIVERY_CHANNEL": AUTO_DELIVERY_CHANNEL,
                    "AUTO_DELIVERY_TARGET": AUTO_DELIVERY_TARGET,
                    "EXPECTED_SCRIPT_BASE_PATH": EXPECTED_SCRIPT_BASE_PATH,
                    "CRITICAL_DIRECT_READ_EXTENSIONS": CRITICAL_DIRECT_READ_EXTENSIONS,
                    "AUDIO_DELIVERY_MESSAGE": AUDIO_DELIVERY_MESSAGE,
                    "ENABLE_PROMPT_TRIMMING": ENABLE_PROMPT_TRIMMING,
                    "TRIM_SKILLS": TRIM_SKILLS,
                    "ENABLE_ATTENTION_FORCER": ENABLE_ATTENTION_FORCER,
                    "ATTENTION_FORCER_TEXT": ATTENTION_FORCER_TEXT,
                    "ENABLE_EMERGENCY_RESCUE": ENABLE_EMERGENCY_RESCUE,
                    "ENABLE_INPUT_RESCUE": ENABLE_INPUT_RESCUE,
                    "EMERGENCY_RESCUES": EMERGENCY_RESCUES
                }, _cfg_out, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}), 500
        try:
            subprocess.Popen([sys.executable, __file__, f"-{SELECTED_PROFILE}", "-restart"])
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}), 500
        return json.dumps({"ok": True, "restarting": True}), 200

    start_time = time.time()
    try:
        ollama_data = request.json
        if not ollama_data: return json.dumps({"error": "No data received"}), 400
            
        original_messages = ollama_data.get('messages', [])
        requested_model = ollama_data.get('model', OPENCLAW_MODEL_ID)
        
        # NACHHER:
        if DEBUG_MODE:
            print(f"\n\n[DEBUG] {'='*60}")
            print(f"[DEBUG] REQUEST RECEIVED: {datetime.now().strftime('%H:%M:%S')}")
        else:
            print(f"\n\n[{datetime.now().strftime('%H:%M:%S')}] --- NEW REQUEST ---")
            if original_messages and original_messages[-1]['role'] == 'user':
                print(f"{original_messages[-1]['content']}")
                print("-" * 40)

        # --- INPUT RESCUE (SHORT-CIRCUITING) ---
        # INPUT_RESCUE is an explicit user-controlled shortcut. If it is enabled,
        # it should behave consistently across all modes, including FULL and
        # COMPAT pass-through. This keeps the feature predictable: pass-through
        # mode still defines how normal LLM traffic is handled, while INPUT_RESCUE
        # remains an intentional pre-LLM shortcut for known commands.
        if ENABLE_INPUT_RESCUE and original_messages:
            last_msg = original_messages[-1]
            if last_msg.get('role') == 'user':
                last_user_msg = last_msg.get('content', '')
                last_user_msg_lower = last_user_msg.lower()
                is_internal_summary_prompt = (
                    'reply with only the slug' in last_user_msg_lower and
                    'conversation summary:' in last_user_msg_lower
                )
                is_session_start_prompt = 'a new session was started via /new or /reset.' in last_user_msg_lower

                if not is_internal_summary_prompt and not is_session_start_prompt:
                    for rescue in EMERGENCY_RESCUES:
                        if all(kw.lower() in last_user_msg_lower for kw in rescue["keywords"]):
                            if DEBUG_MODE:
                                print(f"[DEBUG] INPUT-RESCUE TRIGGERED: Short-circuiting message to {rescue['command']}")
                            return build_short_circuit_response(
                                requested_model,
                                "exec",
                                {"command": rescue["command"]}
                            )

        if _pass_through_cfg != "transparent" and original_messages:
            last_msg = original_messages[-1]
            if last_msg.get('role') == 'tool' and last_msg.get('tool_name') == 'exec':
                running_session_id = extract_running_exec_session_id(last_msg.get('content', ''))
                if running_session_id:
                    if DEBUG_MODE:
                        print(f"[DEBUG] EXEC-PROCESS-RESCUE: Auto-polling running exec session {running_session_id}")
                    return build_short_circuit_response(
                        requested_model,
                        "process",
                        {"action": "poll", "sessionId": running_session_id, "timeout": 30000}
                    )

        # --- TRANSPARENT PASS-THROUGH MODE ---
        # Raw forward with no prompt/content/tool manipulation.
        # Only the model override and Ollama-compatible stream wrapper remain.
        if _pass_through_cfg == "transparent":
            passthrough_data = copy.deepcopy(ollama_data)
            passthrough_data['model'] = LLM_MODEL_IDENTIFIER

            if DEBUG_MODE:
                print(f"[DEBUG] TRANSPARENT_PASS_THROUGH_MODE active — forwarding request without proxy manipulation.")
                print(json.dumps(passthrough_data, indent=2, ensure_ascii=False))
                print(f"[DEBUG] {'-'*60}")
            try:
                raw_req = requests.post(LLM_SERVER_URL, json=passthrough_data, headers=LLM_REQUEST_HEADERS, stream=True, timeout=180)
            except requests.exceptions.Timeout as e:
                print(f"\n[ERROR] Upstream timeout: {e}")
                return json.dumps({"error": "LLM Server Timeout", "details": str(e), "server": LLM_SERVER_URL}), 504
            except requests.exceptions.RequestException as e:
                print(f"\n[ERROR] Upstream request failed: {e}")
                return json.dumps({"error": "LLM Server Request Failed", "details": str(e), "server": LLM_SERVER_URL}), 502
            if raw_req.status_code != 200:
                response_text = raw_req.text
                response_text_lower = response_text.lower()
                if raw_req.status_code == 400 and (
                    "validation errors for validatoriterator" in response_text_lower or
                    "chatcompletionmessagefunctiontoolcallparam" in response_text_lower or
                    ("tool_calls" in response_text_lower and "arguments" in response_text_lower)
                ):
                    print("[ERROR] TRANSPARENT_PASS_THROUGH: upstream rejected the raw request format. Some cloud providers/models do not accept raw tool/history payloads in transparent mode. This is a backend compatibility issue. Try pass_through='full' or 'compat'.")
                    return json.dumps({
                        "error": f"LLM Server Error {raw_req.status_code}",
                        "details": response_text,
                        "hint": "Transparent mode forwarded the raw request unchanged. Some cloud providers or models reject raw tool/history formats. This is a backend compatibility issue, not a proxy bug. Try pass_through='full' or 'compat'."
                    }), 502
                return json.dumps({"error": f"LLM Server Error {raw_req.status_code}", "details": response_text}), 502
            def full_passthrough_stream():
                merged_tools = {}
                full_content = ""
                token_count = 0

                for chunk in raw_req.iter_lines():
                    if not chunk:
                        continue
                    line = chunk.decode('utf-8').strip()
                    if DEBUG_MODE:
                        print(f"[TRANSPARENT-PT] {line}")
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        choices = data.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        if delta.get("content"):
                            full_content += delta["content"]
                            token_count += 1
                        if "tool_calls" in delta:
                            for tc in delta["tool_calls"]:
                                idx = tc.get("index", 0)
                                if idx not in merged_tools:
                                    merged_tools[idx] = {"name": "", "arguments": ""}
                                if "function" in tc:
                                    if tc["function"].get("name"):
                                        merged_tools[idx]["name"] += tc["function"]["name"]
                                    if tc["function"].get("arguments"):
                                        merged_tools[idx]["arguments"] += tc["function"]["arguments"]
                    except Exception:
                        continue

                try:
                    final_tool_calls = []
                    for idx, func in merged_tools.items():
                        if func["name"]:
                            args = func["arguments"]
                            try:
                                args = json.loads(args) if isinstance(args, str) else args
                            except Exception:
                                pass
                            final_tool_calls.append({"function": {"name": func["name"], "arguments": args}})

                    message_obj = {"role": "assistant", "content": full_content}
                    if final_tool_calls:
                        normalized_tool_calls = []
                        for tc in final_tool_calls:
                            func = tc.get("function", {})
                            tool_name, tool_args = rewrite_pdf_read_tool_call(func.get("name"), func.get("arguments"))
                            normalized_tool_calls.append({"function": {"name": tool_name, "arguments": tool_args}})
                        final_tool_calls = normalized_tool_calls
                        message_obj["tool_calls"] = final_tool_calls

                    duration = time.time() - start_time
                    print(f"[DEBUG] Finished in {duration:.2f}s | Tokens: {token_count} | Tokens/s: {(token_count / duration if duration > 0 else 0):.2f}")
                    print(f"[DEBUG] Chars: {len(full_content)} | Tool Calls: {len(final_tool_calls)} | Mode: TRANSPARENT_PASS_THROUGH")
                    if final_tool_calls:
                        for tc in final_tool_calls:
                            print(f"[TOOL_CALL DETECTED: {tc['function']['name']}]")
                            try: print(json.dumps(tc['function']['arguments'], indent=2, ensure_ascii=False))
                            except: print(tc['function']['arguments'])
                    if full_content and full_content != "NO_REPLY":
                        print(f"[TEXT]: {full_content}")
                    elif not final_tool_calls:
                        print(f"[WARNING]: Model is Silent (NO_REPLY).")

                    yield json.dumps({
                        "model": requested_model,
                        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "message": message_obj,
                        "done": False
                    }).encode('utf-8') + b'\n'

                except Exception as e:
                    print(f"[TRANSPARENT-PT ERROR] {e}")

                finally:
                    yield json.dumps({
                        "model": requested_model,
                        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "message": {"role": "assistant", "content": ""},
                        "done": True
                    }).encode('utf-8') + b'\n'

            return Response(full_passthrough_stream(), content_type='application/x-ndjson')

        # --- FULL PASS-THROUGH MODE ---
        # Pass-through forward to the LLM with proxy-side cleanup/recovery still enabled.
        # This keeps the cloud-oriented compatibility behavior intact.
        # Exception: model name is always overridden from the active profile, since openclaw.json
        # always sends the local placeholder model name regardless of which profile is active.
        if FULL_PASS_THROUGH_MODE:
            passthrough_data = copy.deepcopy(ollama_data)
            passthrough_data['model'] = LLM_MODEL_IDENTIFIER
            passthrough_data.pop('tool_choice', None)
            passthrough_data.pop('options', None)
            passthrough_data.pop('parallel_tool_calls', None)
            if 'messages' in passthrough_data:
                passthrough_data['messages'] = sanitize_binary_tool_results(passthrough_data['messages'])

            if passthrough_data.get('messages') and passthrough_data['messages'][0].get('role') == 'system':
                sys_msg = passthrough_data['messages'][0].get('content', '')
                sys_msg = re.sub(r'## Silent Replies.*?(?:Right: NO_REPLY|NO_REPLY)', '', sys_msg, flags=re.DOTALL)
                sys_msg = sys_msg.replace("When you have nothing to say, respond with ONLY: NO_REPLY", "")
                dynamic_pattern = r"(?i)(The current date and time is.*?$|Current Time:.*?$|Date:.*?$)"
                dynamic_parts = re.findall(dynamic_pattern, sys_msg, flags=re.MULTILINE)
                if dynamic_parts:
                    sys_msg = re.sub(dynamic_pattern, "", sys_msg, flags=re.MULTILINE).strip()
                    if len(passthrough_data['messages']) > 1 and passthrough_data['messages'][-1].get('role') == 'user':
                        passthrough_data['messages'][-1]['content'] += "\n\n[System-Info: " + " | ".join(dynamic_parts) + "]"

                if ENABLE_PROMPT_TRIMMING and TRIM_SKILLS:
                    skills_pattern = "|".join(map(re.escape, TRIM_SKILLS))
                    sys_msg = re.sub(fr'<skill>\s*<name>(?:{skills_pattern})</name>.*?</skill>', '', sys_msg, flags=re.DOTALL)
                    sys_msg = re.sub(r'\n\s*\n', '\n', sys_msg)

                passthrough_data['messages'][0]['content'] = sys_msg

            for msg in passthrough_data.get('messages', []):
                if msg.get('role') == 'tool' and isinstance(msg.get('content'), dict):
                    msg['content'] = json.dumps(msg['content'])
                if msg.get('role') == 'assistant' and 'tool_calls' in msg:
                    for tc in msg.get('tool_calls', []):
                        if isinstance(tc, dict) and 'function' in tc:
                            args = tc['function'].get('arguments')
                            if isinstance(args, dict):
                                tc['function']['arguments'] = json.dumps(args)

            removed_tool_protocol = False
            if 'integrate.api.nvidia.com' in LLM_SERVER_URL:
                if 'messages' in passthrough_data:
                    passthrough_data['messages'], removed_tool_protocol = clean_cloud_passthrough_messages(
                        passthrough_data['messages']
                    )
                if 'tools' in passthrough_data:
                    if removed_tool_protocol:
                        passthrough_data.pop('tools', None)
                    else:
                        sanitized_tools = copy.deepcopy(passthrough_data['tools'])
                        for tool in sanitized_tools:
                            sanitize_tool_schema(tool.get('function', {}).get('parameters', {}))
                        passthrough_data['tools'] = sanitized_tools

            if DEBUG_MODE:
                print(f"[DEBUG] FULL_PASS_THROUGH_MODE active — forwarding raw request.")
                if 'integrate.api.nvidia.com' in LLM_SERVER_URL and removed_tool_protocol:
                    print("[DEBUG] FULL_PASS_THROUGH: removed prior tool protocol for NVIDIA compatibility.")
                print(json.dumps(passthrough_data, indent=2, ensure_ascii=False))
                print(f"[DEBUG] {'-'*60}")
            try:
                raw_req = requests.post(LLM_SERVER_URL, json=passthrough_data, headers=LLM_REQUEST_HEADERS, stream=True, timeout=180)
            except requests.exceptions.Timeout as e:
                print(f"\n[ERROR] Upstream timeout: {e}")
                return json.dumps({"error": "LLM Server Timeout", "details": str(e), "server": LLM_SERVER_URL}), 504
            except requests.exceptions.RequestException as e:
                print(f"\n[ERROR] Upstream request failed: {e}")
                return json.dumps({"error": "LLM Server Request Failed", "details": str(e), "server": LLM_SERVER_URL}), 502
            if raw_req.status_code != 200:
                return json.dumps({"error": f"LLM Server Error {raw_req.status_code}", "details": raw_req.text}), 502
            def full_passthrough_stream():
                merged_tools = {}
                full_content = ""
                token_count = 0

                for chunk in raw_req.iter_lines():
                    if not chunk:
                        continue
                    line = chunk.decode('utf-8').strip()
                    if DEBUG_MODE:
                        print(f"[FULL-PT] {line}")
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        choices = data.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        if delta.get("content"):
                            full_content += delta["content"]
                            token_count += 1
                        if "tool_calls" in delta:
                            for tc in delta["tool_calls"]:
                                idx = tc.get("index", 0)
                                if idx not in merged_tools:
                                    merged_tools[idx] = {"name": "", "arguments": ""}
                                if "function" in tc:
                                    if tc["function"].get("name"):
                                        merged_tools[idx]["name"] += tc["function"]["name"]
                                    if tc["function"].get("arguments"):
                                        merged_tools[idx]["arguments"] += tc["function"]["arguments"]
                    except Exception:
                        continue

                try:
                    final_tool_calls = []
                    for idx, func in merged_tools.items():
                        if func["name"]:
                            args = func["arguments"]
                            try:
                                args = json.loads(args) if isinstance(args, str) else args
                            except Exception:
                                args = {}
                            if isinstance(args, dict) and "file" in args and "file_path" not in args:
                                if func["name"] in ("read", "write", "edit"):
                                    args["file_path"] = args["file"]
                            if isinstance(args, dict):
                                for key in ("path", "file_path", "file"):
                                    if key in args and isinstance(args[key], str):
                                        if EXPECTED_SCRIPT_BASE_PATH.startswith('/') and args[key].startswith(EXPECTED_SCRIPT_BASE_PATH.lstrip('/')):
                                            args[key] = '/' + args[key]
                            final_tool_calls.append({"function": {"name": func["name"], "arguments": args}})

                    if not final_tool_calls and full_content:
                        extracted = extract_hallucinated_tools(full_content)
                        if extracted:
                            for obj, start, end in reversed(extracted):
                                if "arguments" not in obj:
                                    continue
                                args_obj = obj["arguments"]
                                if isinstance(args_obj, dict) and "file" in args_obj and "file_path" not in args_obj:
                                    if obj["name"] in ("read", "write", "edit"):
                                        args_obj = dict(args_obj)
                                        args_obj["file_path"] = args_obj["file"]
                                if isinstance(args_obj, dict):
                                    for key in ("path", "file_path", "file"):
                                        if key in args_obj and isinstance(args_obj[key], str):
                                            if EXPECTED_SCRIPT_BASE_PATH.startswith('/') and args_obj[key].startswith(EXPECTED_SCRIPT_BASE_PATH.lstrip('/')):
                                                args_obj = dict(args_obj)
                                                args_obj[key] = '/' + args_obj[key]
                                final_tool_calls.append({"function": {"name": obj["name"], "arguments": args_obj}})
                                full_content = full_content[:start] + full_content[end:]
                            final_tool_calls.reverse()
                    full_content = re.sub(r'<\|tool[^>]*\|>', '', full_content)
                    full_content = re.sub(r'</?think>', '', full_content).strip()
                    if not final_tool_calls and full_content and original_messages and original_messages[-1].get('role') == 'user':
                        last_user_content_lower = original_messages[-1].get('content', '').lower()
                        matched_rescue = any(
                            all(kw.lower() in last_user_content_lower for kw in rescue.get("keywords", []))
                            for rescue in EMERGENCY_RESCUES
                        )
                        file_action_requested = (
                            (
                                "datei" in last_user_content_lower or
                                ".md" in last_user_content_lower or
                                ".txt" in last_user_content_lower or
                                mentions_critical_extension(last_user_content_lower)
                            ) and
                            any(token in last_user_content_lower for token in ("lies", "lese", "read", "schreib", "schreibe", "write", "edit", "bearbeit", "inhalt", "zeige", "gib mir", "lösch", "loesch", "delete", "remove", "erstell", "anleg", "create"))
                        )
                        script_action_requested = EXPECTED_SCRIPT_BASE_PATH.lower() in last_user_content_lower
                        plain_failure_text = re.search(r'(tool result:|fehler|error|failed|konnte nicht|kann nicht|entschuldigung)', full_content, re.IGNORECASE)
                        tool_action_requested = matched_rescue or file_action_requested or script_action_requested
                        if tool_action_requested and not plain_failure_text and 'integrate.api.nvidia.com' not in LLM_SERVER_URL:
                            try:
                                retry_data = copy.deepcopy(passthrough_data)
                                retry_data['tool_choice'] = "required"
                                if DEBUG_MODE:
                                    print("[DEBUG] FULL_PASS_THROUGH: retrying tool request with tool_choice=required.")
                                retry_req = requests.post(LLM_SERVER_URL, json=retry_data, headers=LLM_REQUEST_HEADERS, stream=True, timeout=180)
                                if retry_req.status_code == 200:
                                    retry_merged_tools = {}
                                    retry_full_content = ""
                                    for retry_chunk in retry_req.iter_lines():
                                        if not retry_chunk:
                                            continue
                                        retry_line = retry_chunk.decode('utf-8').strip()
                                        if DEBUG_MODE:
                                            print(f"[FULL-PT RETRY] {retry_line}")
                                        if not retry_line.startswith("data: "):
                                            continue
                                        retry_data_str = retry_line[6:]
                                        if retry_data_str == "[DONE]":
                                            break
                                        try:
                                            retry_obj = json.loads(retry_data_str)
                                            retry_choices = retry_obj.get("choices", [])
                                            if not retry_choices:
                                                continue
                                            retry_delta = retry_choices[0].get("delta", {})
                                            if retry_delta.get("content"):
                                                retry_full_content += retry_delta["content"]
                                                token_count += 1
                                            if "tool_calls" in retry_delta:
                                                for retry_tc in retry_delta["tool_calls"]:
                                                    retry_idx = retry_tc.get("index", 0)
                                                    if retry_idx not in retry_merged_tools:
                                                        retry_merged_tools[retry_idx] = {"name": "", "arguments": ""}
                                                    if "function" in retry_tc:
                                                        if retry_tc["function"].get("name"):
                                                            retry_merged_tools[retry_idx]["name"] += retry_tc["function"]["name"]
                                                        if retry_tc["function"].get("arguments"):
                                                            retry_merged_tools[retry_idx]["arguments"] += retry_tc["function"]["arguments"]
                                        except Exception:
                                            continue

                                    retry_tool_calls = []
                                    for retry_idx, retry_func in retry_merged_tools.items():
                                        if retry_func["name"]:
                                            retry_args = retry_func["arguments"]
                                            try:
                                                retry_args = json.loads(retry_args) if isinstance(retry_args, str) else retry_args
                                            except Exception:
                                                retry_args = {}
                                            if isinstance(retry_args, dict) and "file" in retry_args and "file_path" not in retry_args:
                                                if retry_func["name"] in ("read", "write", "edit"):
                                                    retry_args["file_path"] = retry_args["file"]
                                            if isinstance(retry_args, dict):
                                                for key in ("path", "file_path", "file"):
                                                    if key in retry_args and isinstance(retry_args[key], str):
                                                        if EXPECTED_SCRIPT_BASE_PATH.startswith('/') and retry_args[key].startswith(EXPECTED_SCRIPT_BASE_PATH.lstrip('/')):
                                                            retry_args[key] = '/' + retry_args[key]
                                            retry_tool_calls.append({"function": {"name": retry_func["name"], "arguments": retry_args}})

                                    if not retry_tool_calls and retry_full_content:
                                        retry_extracted = extract_hallucinated_tools(retry_full_content)
                                        if retry_extracted:
                                            for retry_obj, retry_start, retry_end in reversed(retry_extracted):
                                                if "arguments" not in retry_obj:
                                                    continue
                                                retry_args_obj = retry_obj["arguments"]
                                                if isinstance(retry_args_obj, dict) and "file" in retry_args_obj and "file_path" not in retry_args_obj:
                                                    if retry_obj["name"] in ("read", "write", "edit"):
                                                        retry_args_obj = dict(retry_args_obj)
                                                        retry_args_obj["file_path"] = retry_args_obj["file"]
                                                if isinstance(retry_args_obj, dict):
                                                    for key in ("path", "file_path", "file"):
                                                        if key in retry_args_obj and isinstance(retry_args_obj[key], str):
                                                            if EXPECTED_SCRIPT_BASE_PATH.startswith('/') and retry_args_obj[key].startswith(EXPECTED_SCRIPT_BASE_PATH.lstrip('/')):
                                                                retry_args_obj = dict(retry_args_obj)
                                                                retry_args_obj[key] = '/' + retry_args_obj[key]
                                                retry_tool_calls.append({"function": {"name": retry_obj["name"], "arguments": retry_args_obj}})
                                                retry_full_content = retry_full_content[:retry_start] + retry_full_content[retry_end:]
                                            retry_tool_calls.reverse()

                                    if retry_tool_calls:
                                        final_tool_calls = retry_tool_calls
                                        full_content = re.sub(r'<\|tool[^>]*\|>', '', retry_full_content)
                                        full_content = re.sub(r'</?think>', '', full_content).strip()
                            except Exception:
                                pass
                        if tool_action_requested and not plain_failure_text and not final_tool_calls:
                            if full_content:
                                full_content = full_content + "\n\n[NOTICE: The requested tool action was not executed. The model returned plain text instead of a real tool call.]"
                            else:
                                full_content = "The requested tool action was not executed. The model returned plain text instead of a real tool call."

                    message_obj = {"role": "assistant", "content": full_content}
                    if final_tool_calls:
                        normalized_tool_calls = []
                        for tc in final_tool_calls:
                            func = tc.get("function", {})
                            tool_name, tool_args = rewrite_pdf_read_tool_call(func.get("name"), func.get("arguments"))
                            normalized_tool_calls.append({"function": {"name": tool_name, "arguments": tool_args}})
                        final_tool_calls = normalized_tool_calls
                        message_obj["tool_calls"] = final_tool_calls

                    duration = time.time() - start_time
                    print(f"[DEBUG] Finished in {duration:.2f}s | Tokens: {token_count} | Tokens/s: {(token_count / duration if duration > 0 else 0):.2f}")
                    print(f"[DEBUG] Chars: {len(full_content)} | Tool Calls: {len(final_tool_calls)} | Mode: FULL_PASS_THROUGH")
                    if final_tool_calls:
                        for tc in final_tool_calls:
                            print(f"[TOOL_CALL DETECTED: {tc['function']['name']}]")
                            try: print(json.dumps(tc['function']['arguments'], indent=2, ensure_ascii=False))
                            except: print(tc['function']['arguments'])
                    if full_content and full_content != "NO_REPLY":
                        print(f"[TEXT]: {full_content}")
                    elif not final_tool_calls:
                        print(f"[WARNING]: Model is Silent (NO_REPLY).")

                    yield json.dumps({
                        "model": requested_model,
                        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "message": message_obj,
                        "done": False
                    }).encode('utf-8') + b'\n'

                except Exception as e:
                    print(f"[FULL-PT ERROR] {e}")

                finally:
                    yield json.dumps({
                        "model": requested_model,
                        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "message": {"role": "assistant", "content": ""},
                        "done": True
                    }).encode('utf-8') + b'\n'

            return Response(full_passthrough_stream(), content_type='application/x-ndjson')

        # --- COMPAT PASS-THROUGH MODE ---
        # COMPAT_PASS_THROUGH keeps the pass-through architecture intact, but applies
        # a narrow compatibility layer for cloud endpoints that expose an OpenAI-like
        # API while rejecting specific tool/history payload patterns. This mode exists
        # specifically to preserve FULL_PASS_THROUGH as a truly transparent mode.
        # Use COMPAT_PASS_THROUGH only when a provider requires schema/history cleanup.
        if COMPAT_PASS_THROUGH_MODE:
            passthrough_data = copy.deepcopy(ollama_data)
            passthrough_data['model'] = LLM_MODEL_IDENTIFIER
            passthrough_data.pop('tool_choice', None)
            passthrough_data.pop('options', None)
            passthrough_data.pop('parallel_tool_calls', None)
            if 'messages' in passthrough_data:
                passthrough_data['messages'] = sanitize_binary_tool_results(passthrough_data['messages'])

            if passthrough_data.get('messages') and passthrough_data['messages'][0].get('role') == 'system':
                sys_msg = passthrough_data['messages'][0].get('content', '')
                sys_msg = re.sub(r'## Silent Replies.*?(?:Right: NO_REPLY|NO_REPLY)', '', sys_msg, flags=re.DOTALL)
                sys_msg = sys_msg.replace("When you have nothing to say, respond with ONLY: NO_REPLY", "")
                dynamic_pattern = r"(?i)(The current date and time is.*?$|Current Time:.*?$|Date:.*?$)"
                dynamic_parts = re.findall(dynamic_pattern, sys_msg, flags=re.MULTILINE)
                if dynamic_parts:
                    sys_msg = re.sub(dynamic_pattern, "", sys_msg, flags=re.MULTILINE).strip()
                    if len(passthrough_data['messages']) > 1 and passthrough_data['messages'][-1].get('role') == 'user':
                        passthrough_data['messages'][-1]['content'] += "\n\n[System-Info: " + " | ".join(dynamic_parts) + "]"

                if ENABLE_PROMPT_TRIMMING and TRIM_SKILLS:
                    skills_pattern = "|".join(map(re.escape, TRIM_SKILLS))
                    sys_msg = re.sub(fr'<skill>\s*<name>(?:{skills_pattern})</name>.*?</skill>', '', sys_msg, flags=re.DOTALL)
                    sys_msg = re.sub(r'\n\s*\n', '\n', sys_msg)

                passthrough_data['messages'][0]['content'] = sys_msg

            if 'messages' in passthrough_data:
                passthrough_data['messages'], removed_tool_protocol = clean_cloud_passthrough_messages(
                    passthrough_data['messages']
                )
            else:
                removed_tool_protocol = False

            if 'tools' in passthrough_data:
                if removed_tool_protocol:
                    passthrough_data.pop('tools', None)
                else:
                    sanitized_tools = copy.deepcopy(passthrough_data['tools'])
                    for tool in sanitized_tools:
                        sanitize_tool_schema(tool.get('function', {}).get('parameters', {}))
                    passthrough_data['tools'] = sanitized_tools

            if DEBUG_MODE:
                print(f"[DEBUG] COMPAT_PASS_THROUGH_MODE active — forwarding compatibility-sanitized request.")
                if removed_tool_protocol:
                    print("[DEBUG] COMPAT_PASS_THROUGH: removed prior tool protocol from history for cloud compatibility.")
                print(json.dumps(passthrough_data, indent=2, ensure_ascii=False))
                print(f"[DEBUG] {'-'*60}")
            try:
                raw_req = requests.post(LLM_SERVER_URL, json=passthrough_data, headers=LLM_REQUEST_HEADERS, stream=True, timeout=180)
            except requests.exceptions.Timeout as e:
                print(f"\n[ERROR] Upstream timeout: {e}")
                return json.dumps({"error": "LLM Server Timeout", "details": str(e), "server": LLM_SERVER_URL}), 504
            except requests.exceptions.RequestException as e:
                print(f"\n[ERROR] Upstream request failed: {e}")
                return json.dumps({"error": "LLM Server Request Failed", "details": str(e), "server": LLM_SERVER_URL}), 502
            if raw_req.status_code != 200:
                return json.dumps({"error": f"LLM Server Error {raw_req.status_code}", "details": raw_req.text}), 502
            def full_passthrough_stream():
                merged_tools = {}
                full_content = ""
                token_count = 0

                for chunk in raw_req.iter_lines():
                    if not chunk:
                        continue
                    line = chunk.decode('utf-8').strip()
                    if DEBUG_MODE:
                        print(f"[FULL-PT] {line}")
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        choices = data.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        if delta.get("content"):
                            full_content += delta["content"]
                            token_count += 1
                        if "tool_calls" in delta:
                            for tc in delta["tool_calls"]:
                                idx = tc.get("index", 0)
                                if idx not in merged_tools:
                                    merged_tools[idx] = {"name": "", "arguments": ""}
                                if "function" in tc:
                                    if tc["function"].get("name"):
                                        merged_tools[idx]["name"] += tc["function"]["name"]
                                    if tc["function"].get("arguments"):
                                        merged_tools[idx]["arguments"] += tc["function"]["arguments"]
                    except Exception:
                        continue

                try:
                    final_tool_calls = []
                    for idx, func in merged_tools.items():
                        if func["name"]:
                            args = func["arguments"]
                            try:
                                args = json.loads(args) if isinstance(args, str) else args
                            except Exception:
                                args = {}
                            if isinstance(args, dict) and "file" in args and "file_path" not in args:
                                if func["name"] in ("read", "write", "edit"):
                                    args["file_path"] = args["file"]
                            if isinstance(args, dict):
                                for key in ("path", "file_path", "file"):
                                    if key in args and isinstance(args[key], str):
                                        if EXPECTED_SCRIPT_BASE_PATH.startswith('/') and args[key].startswith(EXPECTED_SCRIPT_BASE_PATH.lstrip('/')):
                                            args[key] = '/' + args[key]
                            final_tool_calls.append({"function": {"name": func["name"], "arguments": args}})

                    if not final_tool_calls and full_content:
                        extracted = extract_hallucinated_tools(full_content)
                        if extracted:
                            for obj, start, end in reversed(extracted):
                                if "arguments" not in obj:
                                    continue
                                args_obj = obj["arguments"]
                                if isinstance(args_obj, dict) and "file" in args_obj and "file_path" not in args_obj:
                                    if obj["name"] in ("read", "write", "edit"):
                                        args_obj = dict(args_obj)
                                        args_obj["file_path"] = args_obj["file"]
                                if isinstance(args_obj, dict):
                                    for key in ("path", "file_path", "file"):
                                        if key in args_obj and isinstance(args_obj[key], str):
                                            if EXPECTED_SCRIPT_BASE_PATH.startswith('/') and args_obj[key].startswith(EXPECTED_SCRIPT_BASE_PATH.lstrip('/')):
                                                args_obj = dict(args_obj)
                                                args_obj[key] = '/' + args_obj[key]
                                final_tool_calls.append({"function": {"name": obj["name"], "arguments": args_obj}})
                                full_content = full_content[:start] + full_content[end:]
                            final_tool_calls.reverse()
                    full_content = re.sub(r'<\|tool[^>]*\|>', '', full_content)
                    full_content = re.sub(r'</?think>', '', full_content).strip()
                    if not final_tool_calls and full_content and original_messages and original_messages[-1].get('role') == 'user':
                        last_user_content_lower = original_messages[-1].get('content', '').lower()
                        matched_rescue = any(
                            all(kw.lower() in last_user_content_lower for kw in rescue.get("keywords", []))
                            for rescue in EMERGENCY_RESCUES
                        )
                        file_action_requested = (
                            (
                                "datei" in last_user_content_lower or
                                ".md" in last_user_content_lower or
                                ".txt" in last_user_content_lower or
                                mentions_critical_extension(last_user_content_lower)
                            ) and
                            any(token in last_user_content_lower for token in ("lies", "lese", "read", "schreib", "schreibe", "write", "edit", "bearbeit", "inhalt", "zeige", "gib mir", "lösch", "loesch", "delete", "remove", "erstell", "anleg", "create"))
                        )
                        script_action_requested = EXPECTED_SCRIPT_BASE_PATH.lower() in last_user_content_lower
                        plain_failure_text = re.search(r'(tool result:|fehler|error|failed|konnte nicht|kann nicht|entschuldigung)', full_content, re.IGNORECASE)
                        tool_action_requested = matched_rescue or file_action_requested or script_action_requested
                        if tool_action_requested and not plain_failure_text and 'integrate.api.nvidia.com' not in LLM_SERVER_URL:
                            try:
                                retry_data = copy.deepcopy(passthrough_data)
                                retry_data['tool_choice'] = "required"
                                if DEBUG_MODE:
                                    print("[DEBUG] COMPAT_PASS_THROUGH: retrying tool request with tool_choice=required.")
                                retry_req = requests.post(LLM_SERVER_URL, json=retry_data, headers=LLM_REQUEST_HEADERS, stream=True, timeout=180)
                                if retry_req.status_code == 200:
                                    retry_merged_tools = {}
                                    retry_full_content = ""
                                    for retry_chunk in retry_req.iter_lines():
                                        if not retry_chunk:
                                            continue
                                        retry_line = retry_chunk.decode('utf-8').strip()
                                        if DEBUG_MODE:
                                            print(f"[FULL-PT RETRY] {retry_line}")
                                        if not retry_line.startswith("data: "):
                                            continue
                                        retry_data_str = retry_line[6:]
                                        if retry_data_str == "[DONE]":
                                            break
                                        try:
                                            retry_obj = json.loads(retry_data_str)
                                            retry_choices = retry_obj.get("choices", [])
                                            if not retry_choices:
                                                continue
                                            retry_delta = retry_choices[0].get("delta", {})
                                            if retry_delta.get("content"):
                                                retry_full_content += retry_delta["content"]
                                                token_count += 1
                                            if "tool_calls" in retry_delta:
                                                for retry_tc in retry_delta["tool_calls"]:
                                                    retry_idx = retry_tc.get("index", 0)
                                                    if retry_idx not in retry_merged_tools:
                                                        retry_merged_tools[retry_idx] = {"name": "", "arguments": ""}
                                                    if "function" in retry_tc:
                                                        if retry_tc["function"].get("name"):
                                                            retry_merged_tools[retry_idx]["name"] += retry_tc["function"]["name"]
                                                        if retry_tc["function"].get("arguments"):
                                                            retry_merged_tools[retry_idx]["arguments"] += retry_tc["function"]["arguments"]
                                        except Exception:
                                            continue

                                    retry_tool_calls = []
                                    for retry_idx, retry_func in retry_merged_tools.items():
                                        if retry_func["name"]:
                                            retry_args = retry_func["arguments"]
                                            try:
                                                retry_args = json.loads(retry_args) if isinstance(retry_args, str) else retry_args
                                            except Exception:
                                                retry_args = {}
                                            if isinstance(retry_args, dict) and "file" in retry_args and "file_path" not in retry_args:
                                                if retry_func["name"] in ("read", "write", "edit"):
                                                    retry_args["file_path"] = retry_args["file"]
                                            if isinstance(retry_args, dict):
                                                for key in ("path", "file_path", "file"):
                                                    if key in retry_args and isinstance(retry_args[key], str):
                                                        if EXPECTED_SCRIPT_BASE_PATH.startswith('/') and retry_args[key].startswith(EXPECTED_SCRIPT_BASE_PATH.lstrip('/')):
                                                            retry_args[key] = '/' + retry_args[key]
                                            retry_tool_calls.append({"function": {"name": retry_func["name"], "arguments": retry_args}})

                                    if not retry_tool_calls and retry_full_content:
                                        retry_extracted = extract_hallucinated_tools(retry_full_content)
                                        if retry_extracted:
                                            for retry_obj, retry_start, retry_end in reversed(retry_extracted):
                                                if "arguments" not in retry_obj:
                                                    continue
                                                retry_args_obj = retry_obj["arguments"]
                                                if isinstance(retry_args_obj, dict) and "file" in retry_args_obj and "file_path" not in retry_args_obj:
                                                    if retry_obj["name"] in ("read", "write", "edit"):
                                                        retry_args_obj = dict(retry_args_obj)
                                                        retry_args_obj["file_path"] = retry_args_obj["file"]
                                                if isinstance(retry_args_obj, dict):
                                                    for key in ("path", "file_path", "file"):
                                                        if key in retry_args_obj and isinstance(retry_args_obj[key], str):
                                                            if EXPECTED_SCRIPT_BASE_PATH.startswith('/') and retry_args_obj[key].startswith(EXPECTED_SCRIPT_BASE_PATH.lstrip('/')):
                                                                retry_args_obj = dict(retry_args_obj)
                                                                retry_args_obj[key] = '/' + retry_args_obj[key]
                                                retry_tool_calls.append({"function": {"name": retry_obj["name"], "arguments": retry_args_obj}})
                                                retry_full_content = retry_full_content[:retry_start] + retry_full_content[retry_end:]
                                            retry_tool_calls.reverse()

                                    if retry_tool_calls:
                                        final_tool_calls = retry_tool_calls
                                        full_content = re.sub(r'<\|tool[^>]*\|>', '', retry_full_content)
                                        full_content = re.sub(r'</?think>', '', full_content).strip()
                            except Exception:
                                pass
                        if tool_action_requested and not plain_failure_text and not final_tool_calls:
                            if full_content:
                                full_content = full_content + "\n\n[NOTICE: The requested tool action was not executed. The model returned plain text instead of a real tool call.]"
                            else:
                                full_content = "The requested tool action was not executed. The model returned plain text instead of a real tool call."

                    message_obj = {"role": "assistant", "content": full_content}
                    if final_tool_calls:
                        message_obj["tool_calls"] = final_tool_calls

                    duration = time.time() - start_time
                    print(f"[DEBUG] Finished in {duration:.2f}s | Tokens: {token_count} | Tokens/s: {(token_count / duration if duration > 0 else 0):.2f}")
                    print(f"[DEBUG] Chars: {len(full_content)} | Tool Calls: {len(final_tool_calls)} | Mode: COMPAT_PASS_THROUGH")
                    if final_tool_calls:
                        for tc in final_tool_calls:
                            print(f"[TOOL_CALL DETECTED: {tc['function']['name']}]")
                            try: print(json.dumps(tc['function']['arguments'], indent=2, ensure_ascii=False))
                            except: print(tc['function']['arguments'])
                    if full_content and full_content != "NO_REPLY":
                        print(f"[TEXT]: {full_content}")
                    elif not final_tool_calls:
                        print(f"[WARNING]: Model is Silent (NO_REPLY).")

                    yield json.dumps({
                        "model": requested_model,
                        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "message": message_obj,
                        "done": False
                    }).encode('utf-8') + b'\n'

                except Exception as e:
                    print(f"[FULL-PT ERROR] {e}")

                finally:
                    yield json.dumps({
                        "model": requested_model,
                        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "message": {"role": "assistant", "content": ""},
                        "done": True
                    }).encode('utf-8') + b'\n'

            return Response(full_passthrough_stream(), content_type='application/x-ndjson')
       
        # Loop breaker for Gateway errors - Bypass if in pass-through mode
        if not PASS_THROUGH_MODE and not COMPAT_PASS_THROUGH_MODE and original_messages and original_messages[-1].get('role') == 'tool':
            content_str = str(original_messages[-1].get('content', ''))
            if "No active WhatsApp" in content_str:
                # No WhatsApp listener = cron isolation problem, always stop
                if DEBUG_MODE:
                    print("[DEBUG] Intercepted message tool failure. Stopping loop.")
            elif "Message failed" in content_str:
                # Only stop if the preceding tool_call had no 'to' target
                # (Cron jobs without target fail silently; Heartbeat calls have a proper 'to')
                last_tool_call_has_target = any(
                    tc.get('function', {}).get('arguments', {}).get('to')
                    for msg in original_messages
                    if msg.get('role') == 'assistant'
                    for tc in (msg.get('tool_calls') or [])
                    if tc.get('function', {}).get('name') == 'message'
                )
                if not last_tool_call_has_target:
                    if DEBUG_MODE:
                        print("[DEBUG] Intercepted message tool failure (no target). Stopping loop.")
                else:
                    # Legitimate message call with target — let it through
                    pass
            else:
                content_str = ""  # reset to skip the short_circuit below

        # --- SMART AMNESIA LOGIC ---
        

        apply_amnesia = False
        if not PASS_THROUGH_MODE and ENABLE_SMART_AMNESIA:
            # Amnesia only when the current turn processes a tool result (last message is tool)
            if original_messages and original_messages[-1].get('role') == 'tool':
                apply_amnesia = True

        if apply_amnesia:
            messages = []
            if len(original_messages) > 0:
                messages.append(original_messages[0])
            if len(original_messages) > 1:
                last_msgs = []
                for msg in reversed(original_messages[1:]):
                    last_msgs.append(msg)
                    if msg['role'] == 'user':
                        break
                last_msgs.reverse()
                messages.extend(last_msgs)
            if DEBUG_MODE:
                print(f"[DEBUG] SMART AMNESIA APPLIED: Truncated history to {len(messages)} messages.")
        elif not PASS_THROUGH_MODE and ENABLE_SMART_AMNESIA and len(original_messages) > CHAT_HISTORY_LIMIT + 1:
            # Chat mode: System prompt + last CHAT_HISTORY_LIMIT messages
            messages = [original_messages[0]] + original_messages[-(CHAT_HISTORY_LIMIT):]
            if DEBUG_MODE:
                print(f"[DEBUG] SMART AMNESIA CHAT-TRIM: Kept last {CHAT_HISTORY_LIMIT} messages.")
        else:
            messages = original_messages
            if not PASS_THROUGH_MODE and DEBUG_MODE and len(original_messages) > 1:
                print(f"[DEBUG] SMART AMNESIA OFF: History within limit, preserving full.")

        if _pass_through_cfg != "transparent":
            messages = sanitize_binary_tool_results(messages)

        # Clean up System Prompt & Configurable Trimming for 7B Cognitive Overload
        if not PASS_THROUGH_MODE and messages and messages[0]['role'] == 'system':
            sys_msg = messages[0]['content']
            sys_msg = re.sub(r'## Silent Replies.*?(?:Right: NO_REPLY|NO_REPLY)', '', sys_msg, flags=re.DOTALL)
            sys_msg = sys_msg.replace("When you have nothing to say, respond with ONLY: NO_REPLY", "")
            dynamic_pattern = r"(?i)(The current date and time is.*?$|Current Time:.*?$|Date:.*?$)"
            dynamic_parts = re.findall(dynamic_pattern, sys_msg, flags=re.MULTILINE)
            if dynamic_parts:
                sys_msg = re.sub(dynamic_pattern, "", sys_msg, flags=re.MULTILINE).strip()
                if len(messages) > 1 and messages[-1]['role'] == 'user':
                    messages[-1]['content'] += "\n\n[System-Info: " + " | ".join(dynamic_parts) + "]"
            
            if ENABLE_PROMPT_TRIMMING and TRIM_SKILLS:
                skills_pattern = "|".join(map(re.escape, TRIM_SKILLS))
                sys_msg = re.sub(fr'<skill>\s*<name>(?:{skills_pattern})</name>.*?</skill>', '', sys_msg, flags=re.DOTALL)
                sys_msg = re.sub(r'\n\s*\n', '\n', sys_msg) # Clean up empty lines
            
            messages[0]['content'] = sys_msg

        # Attention-Forcer Injection for small models
        if not PASS_THROUGH_MODE and ENABLE_ATTENTION_FORCER and messages and messages[-1].get('role') == 'user':
            last_user_content_lower = messages[-1].get('content', '').lower()
            attention_forcer_needed = EXPECTED_SCRIPT_BASE_PATH.lower() in last_user_content_lower
            if not attention_forcer_needed:
                for rescue in EMERGENCY_RESCUES:
                    if any(kw.lower() in last_user_content_lower for kw in rescue.get("keywords", [])):
                        attention_forcer_needed = True
                        break
            if attention_forcer_needed and ATTENTION_FORCER_TEXT not in messages[-1]['content']:
                messages[-1]['content'] += ATTENTION_FORCER_TEXT

        # Format sanitization for OpenAI endpoint (Required for both modes to prevent 400 Bad Request)
        for msg in messages:
            if msg.get('role') == 'tool' and isinstance(msg.get('content'), dict):
                 msg['content'] = json.dumps(msg['content'])
            if msg.get('role') == 'assistant' and 'tool_calls' in msg:
                 for tc in msg.get('tool_calls', []):
                     if isinstance(tc, dict) and 'function' in tc:
                         args = tc['function'].get('arguments')
                         if isinstance(args, dict):
                             tc['function']['arguments'] = json.dumps(args)

        openai_payload = {
            "model": LLM_MODEL_IDENTIFIER,
            "messages": messages,
            "stream": True,
            "temperature": ollama_data.get('options', {}).get('temperature', 0.0),
            "max_tokens": ollama_data.get('options', {}).get('num_predict', 4096)
        }

        if 'tools' in ollama_data:
            if not PASS_THROUGH_MODE:
                # Filter tools to remove web_search (forces web_fetch)
                filtered_tools = []
                for t in ollama_data['tools']:
                    if t.get('function', {}).get('name') == 'web_search':
                        continue
                    filtered_tools.append(t)
                
                tools = filtered_tools
                
                if not any(t.get('function', {}).get('name') == 'exec' for t in tools):
                    tools.append({
                        "type": "function",
                        "function": {
                            "name": "exec",
                            "description": "Run shell commands natively on the system.",
                            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}
                        }
                    })
                if not any(t.get('function', {}).get('name') == 'upload_audio' for t in tools):
                    tools.append({
                        "type": "function",
                        "function": {
                            "name": "upload_audio",
                            "description": "Uploads an audio file as a voice message.",
                            "parameters": {"type": "object", "properties": {"media": {"type": "string"}}, "required": ["media"]}
                        }
                    })
                openai_payload['tools'] = tools
            else:
                # In PASS_THROUGH_MODE, simply forward the tools unmodified
                openai_payload['tools'] = ollama_data['tools']

        if DEBUG_MODE:
            print(f"[DEBUG] OUTGOING PAYLOAD TO LLM:")
            if PASS_THROUGH_MODE:
                print(f"[DEBUG] (PASS_THROUGH_MODE IS ACTIVE - Content sent unmodified)")
            print(json.dumps(openai_payload, indent=2, ensure_ascii=False))
            print(f"[DEBUG] {'-'*60}")

        try:
            req = requests.post(LLM_SERVER_URL, json=openai_payload, headers=LLM_REQUEST_HEADERS, stream=True, timeout=180)
        except requests.exceptions.Timeout as e:
            print(f"\n[ERROR] Upstream timeout: {e}")
            return json.dumps({"error": "LLM Server Timeout", "details": str(e), "server": LLM_SERVER_URL}), 504
        except requests.exceptions.RequestException as e:
            print(f"\n[ERROR] Upstream request failed: {e}")
            return json.dumps({"error": "LLM Server Request Failed", "details": str(e), "server": LLM_SERVER_URL}), 502
        if req.status_code != 200:
            return json.dumps({"error": f"LLM Server Error {req.status_code}", "details": req.text}), 502

        def generate():
            token_count = 0
            full_content = ""
            merged_tools = {}
            
            # --- Detect if this conversation originated from a Cron job ---
            is_cron_job = False
            for m in reversed(messages):
                if m.get('role') == 'user':
                    if '[cron:' in m.get('content', '').lower():
                        is_cron_job = True
                    break
            # Also detect systemEvent jobs (no [cron:] prefix, but inbound_meta channel=cron-event)
            if not is_cron_job and messages and messages[0].get('role') == 'system':
                if '"channel": "cron-event"' in messages[0].get('content', ''):
                    is_cron_job = True
            
            for line in req.iter_lines():
                if not line: continue
                line_text = line.decode('utf-8')
                if line_text.startswith("data: "):
                    data_str = line_text[6:].strip()
                    if data_str == "[DONE]": break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk['choices'][0]['delta']
                        if 'content' in delta and delta['content']:
                            full_content += delta['content']
                            token_count += 1
                        if 'tool_calls' in delta:
                            for tc in delta['tool_calls']:
                                idx = tc.get('index', 0)
                                if idx not in merged_tools: merged_tools[idx] = {"name": "", "arguments": ""}
                                if 'function' in tc:
                                    func = tc['function']
                                    if 'name' in func and func['name']: merged_tools[idx]["name"] += func['name']
                                    if 'arguments' in func and func['arguments']: merged_tools[idx]["arguments"] += func['arguments']
                    except Exception: continue
            
            duration = time.time() - start_time
            full_content = full_content.replace('<|im_end|>', '').replace('<|im_start|>', '').replace('<|endoftext|>', '').strip()
            
            final_tool_calls = []
            for idx, func in merged_tools.items():
                if func["name"]: final_tool_calls.append({"function": {"name": func["name"], "arguments": func["arguments"]}})
                        
            BT = chr(96)
            
            if not PASS_THROUGH_MODE:
                if not final_tool_calls and full_content:
                    extracted = extract_hallucinated_tools(full_content)
                    if extracted:
                        for obj, start, end in reversed(extracted):
                            if "arguments" not in obj:
                                continue
                            args_str = obj["arguments"] if isinstance(obj["arguments"], str) else json.dumps(obj["arguments"])
                            final_tool_calls.append({"function": {"name": obj["name"], "arguments": args_str}})
                            full_content = full_content[:start] + full_content[end:]
                        
                        final_tool_calls.reverse()

                if not final_tool_calls:
                    last_user_content_lower = ""
                    if original_messages and original_messages[-1].get('role') == 'user':
                        last_user_content_lower = original_messages[-1].get('content', '').lower()
                    matched_rescue = any(
                        all(kw.lower() in last_user_content_lower for kw in rescue.get("keywords", []))
                        for rescue in EMERGENCY_RESCUES
                    ) if last_user_content_lower else False
                    file_action_requested = (
                        (
                            "datei" in last_user_content_lower or
                            ".md" in last_user_content_lower or
                            ".txt" in last_user_content_lower or
                            mentions_critical_extension(last_user_content_lower)
                        ) and
                        any(token in last_user_content_lower for token in ("lies", "lese", "read", "schreib", "schreibe", "write", "edit", "bearbeit", "inhalt", "zeige", "gib mir", "lösch", "loesch", "delete", "remove", "erstell", "anleg", "create", "konvertier", "convert", "umwandel"))
                    ) if last_user_content_lower else False
                    script_action_requested = EXPECTED_SCRIPT_BASE_PATH.lower() in last_user_content_lower if last_user_content_lower else False
                    plain_failure_text = re.search(r'(tool result:|fehler|error|failed|konnte nicht|kann nicht|entschuldigung)', full_content, re.IGNORECASE)
                    tool_action_requested = matched_rescue or file_action_requested or script_action_requested
                    if tool_action_requested and not plain_failure_text:
                        try:
                            retry_payload = copy.deepcopy(openai_payload)
                            retry_payload['tool_choice'] = "required"
                            if DEBUG_MODE:
                                print("[DEBUG] OFF-MODE: retrying tool request with tool_choice=required.")
                            retry_req = requests.post(LLM_SERVER_URL, json=retry_payload, headers=LLM_REQUEST_HEADERS, stream=True, timeout=180)
                            if retry_req.status_code == 200:
                                retry_merged_tools = {}
                                retry_full_content = ""
                                for retry_line in retry_req.iter_lines():
                                    if not retry_line:
                                        continue
                                    retry_line_text = retry_line.decode('utf-8').strip()
                                    if DEBUG_MODE:
                                        print(f"[OFF RETRY] {retry_line_text}")
                                    if not retry_line_text.startswith("data: "):
                                        continue
                                    retry_data_str = retry_line_text[6:]
                                    if retry_data_str == "[DONE]":
                                        break
                                    try:
                                        retry_chunk = json.loads(retry_data_str)
                                        retry_delta = retry_chunk['choices'][0]['delta']
                                        if 'content' in retry_delta and retry_delta['content']:
                                            retry_full_content += retry_delta['content']
                                            token_count += 1
                                        if 'tool_calls' in retry_delta:
                                            for retry_tc in retry_delta['tool_calls']:
                                                retry_idx = retry_tc.get('index', 0)
                                                if retry_idx not in retry_merged_tools:
                                                    retry_merged_tools[retry_idx] = {"name": "", "arguments": ""}
                                                if 'function' in retry_tc:
                                                    retry_func = retry_tc['function']
                                                    if 'name' in retry_func and retry_func['name']:
                                                        retry_merged_tools[retry_idx]["name"] += retry_func['name']
                                                    if 'arguments' in retry_func and retry_func['arguments']:
                                                        retry_merged_tools[retry_idx]["arguments"] += retry_func['arguments']
                                    except Exception:
                                        continue

                                retry_tool_calls = []
                                for retry_idx, retry_func in retry_merged_tools.items():
                                    if retry_func["name"]:
                                        retry_tool_calls.append({"function": {"name": retry_func["name"], "arguments": retry_func["arguments"]}})

                                if not retry_tool_calls and retry_full_content:
                                    retry_extracted = extract_hallucinated_tools(retry_full_content)
                                    if retry_extracted:
                                        for retry_obj, retry_start, retry_end in reversed(retry_extracted):
                                            if "arguments" not in retry_obj:
                                                continue
                                            retry_args_str = retry_obj["arguments"] if isinstance(retry_obj["arguments"], str) else json.dumps(retry_obj["arguments"])
                                            retry_tool_calls.append({"function": {"name": retry_obj["name"], "arguments": retry_args_str}})
                                            retry_full_content = retry_full_content[:retry_start] + retry_full_content[retry_end:]
                                        retry_tool_calls.reverse()

                                if retry_tool_calls:
                                    final_tool_calls = retry_tool_calls
                                    full_content = retry_full_content.strip()
                        except Exception:
                            pass

                    if tool_action_requested and not plain_failure_text and not final_tool_calls:
                        if full_content:
                            full_content = full_content + "\n\n[NOTICE: The requested file or tool action was not executed. The model returned plain text instead of a real tool call.]"

                if not final_tool_calls and full_content:
                    rescued_command = None
                    match_start = -1
                    escaped_base = re.escape(EXPECTED_SCRIPT_BASE_PATH)
                    
                    bash_re = BT * 3 + r'(?:[a-zA-Z]*)?\s*\n(.*?' + escaped_base + r'.*?)\n' + BT * 3
                    bash_match = re.search(bash_re, full_content, re.DOTALL)
                    
                    if bash_match:
                        command = bash_match.group(1).strip()
                        if command:
                            final_tool_calls.append({"function": {"name": "exec", "arguments": json.dumps({"command": command})}})
                            full_content = full_content[:bash_match.start()].strip()
                            if DEBUG_MODE:
                                print(f"[DEBUG] BASH-RESCUE: Converted code block to exec call: {command}")
                    else:
                        naked_match = re.search(r'(?m)^((?:bash\s+)?' + escaped_base + r'[^\n]+)$', full_content)
                        if naked_match: 
                            command = naked_match.group(1).strip()
                            final_tool_calls.append({"function": {"name": "exec", "arguments": json.dumps({"command": command})}})
                            full_content = full_content[:naked_match.start()].strip()
                            if DEBUG_MODE:
                                print(f"[DEBUG] BASH-RESCUE: Converted naked string to exec call: {command}")
                        elif ENABLE_EMERGENCY_RESCUE:
                            content_lower = full_content.lower()
                            for rescue in EMERGENCY_RESCUES:
                                if all(kw.lower() in content_lower for kw in rescue["keywords"]):
                                    command = rescue["command"]
                                    
                                    # Anti-Loop Check: Was this exact command already executed in the current history?
                                    already_executed = False
                                    for m in original_messages:
                                        if m.get('role') == 'assistant' and 'tool_calls' in m:
                                            for tc in m.get('tool_calls', []):
                                                if isinstance(tc, dict) and tc.get('function', {}).get('name') == 'exec':
                                                    args = tc.get('function', {}).get('arguments', '')
                                                    if isinstance(args, str) and command in args:
                                                        already_executed = True
                                                    elif isinstance(args, dict) and command in args.get('command', ''):
                                                        already_executed = True
                                                        
                                    if already_executed:
                                        continue # Skip this rescue so the LLM can actually answer the user!
                                        
                                    final_tool_calls.append({"function": {"name": "exec", "arguments": json.dumps({"command": command})}})
                                    full_content = full_content[:0].strip()
                                    if DEBUG_MODE:
                                        print(f"[DEBUG] BASH-RESCUE: Triggered emergency rescue to exec call: {command}")
                                    break

                if not PASS_THROUGH_MODE and not final_tool_calls and not full_content and original_messages and original_messages[-1].get('role') == 'tool':
                    try:
                        retry_payload = copy.deepcopy(openai_payload)
                        retry_payload.pop('tools', None)
                        retry_payload['messages'] = copy.deepcopy(messages)
                        retry_payload['messages'].append({
                            "role": "user",
                            "content": "Use the most recent tool result to answer the user's request directly. Do not call another tool. Do not reply with NO_REPLY."
                        })
                        if DEBUG_MODE:
                            print("[DEBUG] TOOL-RESULT-RETRY: retrying empty post-tool response without tools.")
                        retry_req = requests.post(LLM_SERVER_URL, json=retry_payload, headers=LLM_REQUEST_HEADERS, stream=True, timeout=180)
                        if retry_req.status_code == 200:
                            retry_full_content = ""
                            for retry_line in retry_req.iter_lines():
                                if not retry_line:
                                    continue
                                retry_line_text = retry_line.decode('utf-8')
                                if DEBUG_MODE:
                                    print(f"[TOOL-RESULT-RETRY] {retry_line_text}")
                                if not retry_line_text.startswith("data: "):
                                    continue
                                retry_data_str = retry_line_text[6:].strip()
                                if retry_data_str == "[DONE]":
                                    break
                                try:
                                    retry_chunk = json.loads(retry_data_str)
                                    retry_delta = retry_chunk['choices'][0]['delta']
                                    if 'content' in retry_delta and retry_delta['content']:
                                        retry_full_content += retry_delta['content']
                                        token_count += 1
                                except Exception:
                                    continue
                            if retry_full_content:
                                full_content = retry_full_content.strip()
                    except Exception:
                        pass

                pattern_empty_block = BT * 3 + r'(?:json)?\s*' + BT * 3
                full_content = re.sub(pattern_empty_block, '', full_content)
                pattern_dangling = r'\s*' + BT * 3 + r'(?:json)?\s*$'
                full_content = re.sub(pattern_dangling, '', full_content)
                full_content = re.sub(r'</?tool_[a-z_]+>', '', full_content)
                full_content = re.sub(r'\n{3,}', '\n\n', full_content).strip()

                cleaned_text = full_content.replace(BT * 3 + 'json', '').replace(BT * 3, '').strip()
                if cleaned_text == "NO_REPLY" or '{"name":"NO_REPLY"}' in cleaned_text.replace(" ", "").replace("\n", ""):
                    full_content = "NO_REPLY"

                for tc in final_tool_calls:
                    if tc['function']['name'] == 'upload_audio':
                        tc['function']['name'] = 'message'
                        try:
                            args = json.loads(tc['function']['arguments'])
                            args['action'] = 'send'
                            args['channel'] = AUTO_DELIVERY_CHANNEL
                            if AUTO_DELIVERY_TARGET: args['target'] = AUTO_DELIVERY_TARGET
                            args['message'] = AUDIO_DELIVERY_MESSAGE
                            tc['function']['arguments'] = json.dumps(args)
                        except: pass

                filtered_tool_calls = []
                for tc in final_tool_calls:
                    if tc.get('function', {}).get('name') == 'exec':
                        try:
                            exec_args = tc['function']['arguments']
                            exec_args = json.loads(exec_args) if isinstance(exec_args, str) else exec_args
                        except Exception:
                            exec_args = {}
                        missing_script_path = extract_missing_exec_script_path(exec_args.get('command'))
                        if missing_script_path:
                            if DEBUG_MODE:
                                print(f"[DEBUG] EXEC-GUARD: blocked nonexistent local script path: {missing_script_path}")
                            notice = f"[NOTICE: The requested exec action was not executed because the referenced local script path does not exist: {missing_script_path}]"
                            full_content = (full_content + "\n\n" + notice).strip() if full_content else notice
                            continue
                    filtered_tool_calls.append(tc)
                final_tool_calls = filtered_tool_calls

                # --- THE LOOP BREAKER ---
                # Only suppress Auto-Delivery if the CURRENT response already contains a 'message' call.
                # Checking full history caused false positives (e.g., the greeting call blocked all future deliveries).
                message_tool_used = any(
                    tc.get('function', {}).get('name') == 'message'
                    for tc in final_tool_calls
                )

                # --- UNIVERSAL AUTO-DELIVERY ---
                # Force delivery to WhatsApp if it's a cron job, because Cron lacks a native chat interface
                should_auto_deliver = FORCE_AUTO_DELIVERY or (FORCE_CRON_DELIVERY and is_cron_job)
                
                if should_auto_deliver and full_content and full_content != "NO_REPLY":
                    if not final_tool_calls and not message_tool_used:  
                        auto_args = {
                            "action": "send",
                            "channel": AUTO_DELIVERY_CHANNEL,
                            "message": full_content
                        }
                        if AUTO_DELIVERY_TARGET:
                            auto_args["target"] = AUTO_DELIVERY_TARGET

                        auto_push_tool = {
                            "function": {
                                "name": "message",
                                "arguments": json.dumps(auto_args)
                            }
                        }
                        final_tool_calls.append(auto_push_tool)
                        if DEBUG_MODE:
                            reason = "Cron Job" if is_cron_job and not FORCE_AUTO_DELIVERY else "Global Config"
                            print(f"[SYSTEM] Auto-Delivery active ({reason})! Text routed to {AUTO_DELIVERY_CHANNEL}.")
                        full_content = "NO_REPLY"
                    elif not final_tool_calls and message_tool_used:
                        if DEBUG_MODE:
                            print("[SYSTEM] Loop-Breaker active: 'message' tool was already used. Suppressing Auto-Delivery.")

            print(f"[DEBUG] Finished in {duration:.2f}s | Tokens: {token_count} | Tokens/s: {(token_count / duration if duration > 0 else 0):.2f}")
            print(f"[DEBUG] Chars: {len(full_content)} | Tool Calls: {len(final_tool_calls)} | Mode: {'PASS_THROUGH' if PASS_THROUGH_MODE else 'OFF'}")
            
            if final_tool_calls:
                for tc in final_tool_calls:
                    print(f"[TOOL_CALL DETECTED: {tc['function']['name']}]")
                    try: print(json.dumps(json.loads(tc['function']['arguments']), indent=2))
                    except: print(tc['function']['arguments'])
                    
            if full_content and full_content != "NO_REPLY": 
                print(f"[TEXT]: {full_content}")
            elif not final_tool_calls: 
                print(f"[WARNING]: Model is Silent (NO_REPLY).")

            message_obj = {"role": "assistant", "content": full_content if full_content != "NO_REPLY" else ""}
            if final_tool_calls:
                output_tool_calls = []
                for tc in final_tool_calls:
                    tool_name, args = rewrite_pdf_read_tool_call(tc['function']['name'], tc['function']['arguments'])
                    try: args = json.loads(args) if isinstance(args, str) else args
                    except: args = {}
                    if isinstance(args, dict):
                        for key in ("path", "file_path", "file"):
                            if key in args and isinstance(args[key], str):
                                if EXPECTED_SCRIPT_BASE_PATH.startswith('/') and args[key].startswith(EXPECTED_SCRIPT_BASE_PATH.lstrip('/')):
                                    args[key] = '/' + args[key]
                    output_tool_calls.append({"function": {"name": tool_name, "arguments": args}})
                message_obj["tool_calls"] = output_tool_calls

            yield json.dumps({"model": requested_model, "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "message": message_obj, "done": False}).encode('utf-8') + b'\n'
            yield json.dumps({"model": requested_model, "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "message": {"role": "assistant", "content": ""}, "done": True}).encode('utf-8') + b'\n'

        return Response(generate(), content_type='application/x-ndjson')
    except Exception as e:
        print(f"\n[ERROR] Proxy error: {e}")
        return json.dumps({"error": str(e)}), 502

if __name__ == '__main__':
    if '-stop' in sys.argv:
        kill_other_instances()
        sys.exit(0)
        
    if '-restart' in sys.argv:
        kill_other_instances()

    print(f"==========================================")
    print(f"ClawCut Universal Proxy (V4.10.24)")
    _profile_target = cfg.get('base_url', f"{cfg.get('ip', '?')}:{cfg.get('port', '?')}")
    print(f"PROFILE SELECTED: {SELECTED_PROFILE.upper()} ({_profile_target})")
    print(f"MODEL USED: {cfg['model_name']}")
    _pt_label = "TRANSPARENT" if _pass_through_cfg == "transparent" else ("FULL" if FULL_PASS_THROUGH_MODE else ("COMPAT" if COMPAT_PASS_THROUGH_MODE else ("SMALL" if PASS_THROUGH_MODE else "OFF")))
    print(f"PASS_THROUGH_MODE = {_pt_label}")
    if not PASS_THROUGH_MODE and not FULL_PASS_THROUGH_MODE and not COMPAT_PASS_THROUGH_MODE and _pass_through_cfg != "transparent":
        print(f"SMART_AMNESIA = {ENABLE_SMART_AMNESIA}")
        print(f"AUTO_DELIVERY = {FORCE_AUTO_DELIVERY} (Cron: {FORCE_CRON_DELIVERY}) -> {AUTO_DELIVERY_CHANNEL}:{AUTO_DELIVERY_TARGET}")
    if WRITE_TO_LOGFILE: print(f"LOGGING TO: {PATH_TO_LOGFILE} (Max Size: {DELETE_LOG_SIZE})")
    print(f"==========================================")
    app.run(host='0.0.0.0', port=5000, debug=DEBUG_MODE, use_reloader=False)
