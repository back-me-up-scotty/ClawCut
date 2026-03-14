## ClawCut Proxy

OpenClaw is a powerful framework that, by default, sends massive system prompts 
(often >28,000 characters) and complex tool definitions (JSON tools) to the LLM. 
While large cloud models or high-end local models (14B etc.) handle this well, 
small models (7B, 8B) running on limited hardware (Mac/MLX or Raspberry Pi) 
often suffer from "Cognitive Overload". This is where ClawCut steps in.

ClawCut is an experimental proxy to manipulate, inject JSON-Calls and 
extract JSON clutter from OpenClaw. 

## NOTE PRIOR TO INSTALLATION

There is no guarantee that ClawCut will work in every 
configuration or with future OpenClaw updates. It is very likely that 
this is not the case. ClawCut therefore depends on the community for 
its further development. 

In other words: Make ClawCut your own. Try to  Share your results.

To give you an idea of the setup I use to run my LLM locally in combination 
with OpenClaw and ClawCut:

LLM 1:

- MacMini M4 Pro 24 GB RAM
- mlx-community/Qwen2.5-Coder-7B-Instruct-4bit

LLM 2:

- Windows 10
- RTX 3060 12 GB VRAM
- 128 GB RAM
- Ollama qwen2.5:14b

OpenClaw & ClawCut

- Raspberry 5
- 16 GB RAM

## USING CLAWCUT CAN SOLVE FOLLOWING ISSUES:
 
- Extreme processing latency (slow Time To First Token).
- Forgetting their identity or available tools.
- Hallucinating text answers instead of executing local scripts.
- Connection timeouts or malformed JSON responses.
- Huge RAM consumption

This proxy acts as a "Man-in-the-Middle" between OpenClaw and your local LLM 
server to optimize the data flow:

-  PROMPT TRIMMING: Automatically removes unused default skills from the system 
   prompt to keep the context window small and focused.
-  SMART AMNESIA: Intelligently truncates chat history after successful tool 
   executions to free up "mental space" for the model.
-  ATTENTION FORCER: Injects a reminder at the very end of the user query to 
   ensure the model prioritizes tool usage.
-  TOOL FORCER: Injects keywords for tool calling and points to commands.
-  INPUT RESCUE: Short-circuits known incoming requests (like Cron-Jobs) to 
   bypass LLM latency and ensure 100% reliability for automated tasks.
-  BASH-RESCUE: Detects poorly formatted script calls (e.g., naked code blocks) 
   and converts them into valid OpenClaw tool calls on the fly.
-  Automatically filters dynamic timestamps from system prompts to enable near-instant 
   responses via hardware caching.
-  Translates between OpenAI-compatible streams (MLX) and the Ollama/NDJSON 
   format expected by OpenClaw.
-  Real-time console output of prefill duration, token count, and generation 
   speed (tokens per second).
-  With the **DEBUG\_MODE** enabled, you can inspect the full "JSON Clutter" 
   sent by OpenClaw to understand exactly what the model is processing.

## PERFORMANCE
- Significantly faster response times (TTFT), as the model has much less text 
  to process upfront.
- Improved reliability when using and calling scripts (bash or whatever).
- Robust error handling for stream interruptions or formatting errors.

## WHEN TO USE
- Ideal for small models (7B-8B) running on hardware like Mac (MLX), Windows 
  or Linux.
- If your model "chats" too much instead of executing commands.

## WHEN TO USE WITH CAUTION
- If you are using highly intelligent, large models (14B+) that can handle 
  complex prompts natively. In this case, the proxy can act purely as a logger 
  and format translator without manipulating the content if `PASS_THROUGH_MODE = True`.

## CONFIGURE CLAWCUT

**Configuration Profiles**

Edit the clawcut.py file and adjust the profiles. If you have to LLM running, you can switch between both profiles.

Use `"port": 8080` for example if theres a flask server running. Change port, if neccessary. 
Use `"port": 11434` for example if Ollama is running. Change port, if neccessary.
* `IP:` The (local) IP address of your LLM-Host (e.g., `192.168.0.5` if it's on a remote machine or `127.0.0.1` if ClawCut and OpenClaw running on the same machine ).  
* `model_id:` The exact model ID used in your openclaw.json.  
* `model_name:` The name of the model in your openclaw.json.  


```bash
PROFILES = {
    "LLM1": {
        "ip": "192.168.0.xxx",
        "port": 8080, 
        "model_id": "ollama/Qwen2.5-Coder-7B-Instruct-4bit",
        "model_name": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
    },
    "LLM2": {
        "ip": "192.168.0.xxx",
        "port": 11434, 
        "model_id": "ollama/qwen2.5:14b",
        "model_name": "qwen2.5:14b"
    }
}
```

**Note for OpenClaw Configuration (e.g., openclaw.json)**

When using this proxy, the specific model name you configure in OpenClaw does NOT matter.
The proxy intercepts the traffic and completely overrides the requested model 
based on the selected profile below. 

You only need to ensure your OpenClaw provider URL points to the proxy: 
`"http://127.0.0.1:5000/v1"`


**Logging & Storage Config**

`DEBUG_MODE = True` prints the full JSON payloads to the console (useful for troubleshooting).

`WRITE_TO_LOGFILE` saves the terminal output to the specified PATH_TO_LOGFILE.

`DELETE_LOG_SIZE` rotates/deletes the log automatically when it reaches this size to prevent disk full issues.

Location/path to logs. Examples:
 
Linux/Pi: `"/home/username/"` 
Mac: `"/Users/username/"`
Windows: `"C:/Users/username/"`

```bash
DEBUG_MODE = True
WRITE_TO_LOGFILE = True
PATH_TO_LOGFILE = '/home/user/clawcut.log' # Change to your preferred log path
DELETE_LOG_SIZE = '10 MB' 
```

## SMART AMNESIA MODE

Over time, chat histories get too long for small models to process efficiently.
If enabled, the proxy watches the current turn: when the last message is a tool 
result (i.e., the model just received the output of an exec call), the proxy 
truncates all prior chat history. This creates a "fresh start" for the model 
to formulate its response based on the tool result alone, preventing infinite 
loops and keeping the context window and RAM small.

Outside of tool execution turns, normal chat history is preserved up to 
`CHAT_HISTORY_LIMIT` messages (see below).

```bash
ENABLE_SMART_AMNESIA = True
```

## CHAT HISTORY

Amnesia only when the current turn processes a tool result (last message is ‘tool’)
Example: You exchanged 10 messages without calling a tool. The context is preserved. 
Then you call a tool (exec). Result: The context is cut off, and you can no longer 
retrieve the conversation that took place before the tool was called. 

`CHAT_HISTORY_LIMIT` lets you specify how much chat history (recent messages in a normal chat) 
should be kept.

```bash
CHAT_HISTORY_LIMIT = 10 # Number of messages (excluding system messages) in chat mode
```


## UNIVERSAL AUTO-DELIVERY

Before OpenClaw 3.12, the proxy had to manually force the LLM to send its text answers to WhatsApp using the 'message' tool.
For OpenClaw 3.12+, better keep this `FALSE`. 

OpenClaw now has "native reply routing" and should automatically route text answers back to the chat interface.
Setting this to True on modern OpenClaw versions could cause a "Message failed" conflict.

This is legacy support.

```bash
FORCE_AUTO_DELIVERY = False
```

Automatically force text delivery to WhatsApp if the request originated from a Cron job.
Cron jobs lack a native chat interface, so OpenClaw's native routing won't show the text anywhere.

```bash
FORCE_CRON_DELIVERY = False
AUTO_DELIVERY_CHANNEL = "whatsapp"  
AUTO_DELIVERY_TARGET = "+49123456" 
```

Important: Since OpenClaw version 2026.3.12 there seems to be issues with the routing of messages triggered by a cron job. 
ClawCut clearly sees this messages. The issue seems to be on OpenClaw's side. FORCE_CRON_DELIVERY has unfortunately 
no effect at the moment. OpenClaw ignores it. This is legacy support.

## PASSTHROUGH MODE

Pure Pass-Through Mode: If True, completely disables all proxy logic (trimming, amnesia, auto-delivery, bash-rescue).
The proxy will only log traffic and forward the exact JSON between OpenClaw and the LLM, maintaining format compatibility.
Useful for powerful models (e.g., 14B, 70B, GPT-4) that don't need workarounds. 
 
If passthrough mode is active (True), you'll immediately notice a difference in speed. Responses from the model are 
generated much more slowly, and tool execution on small models will likely no longer work because the proxy no longer injects tool calls,
and the model becomes overwhelmed again by the massive increase in JSON clutter.

```bash
PASS_THROUGH_MODE = False  # Set "False" to unleash ClawCuts power
```

## BASE PATH FOR SCRIPT RESCUE

Change this to match the root directory where your scripts (if you have some) are stored, that OpenClaw should execute.
This matches what you tell the LLM for example in your TOOLS.md. See also `EMERGENCY_RESCUES`.

Linux/Pi: "/home/username/" 
Mac: "/Users/username/"
Windows: "C:/Users/username/"

```bash
EXPECTED_SCRIPT_BASE_PATH = "/home/user/"
```

SYSTEM PROMPT TRIMMING 

If True, the proxy aggressively strips out the skills listed in TRIM_SKILLS before sending 
the prompt to the model, freeing up its attention span for your custom tools, to prevent cognitive overload.
Change this list to whatever you feel is (un)necessary.

```bash
ENABLE_PROMPT_TRIMMING = True
TRIM_SKILLS = [
    "clawhub", "gemini", "gh-issues", "github", "healthcheck", 
    "nano-pdf", "openai-whisper", "skill-creator", "summarize", 
    "video-frames", "wacli", "weather"
]
```

## ATTENTION FORCER (End-of-Prompt Injection)

If True, this injects a strong reminder at the very end of the user's latest message. Change thist to whatever you want 
your LLM focus on.

```bash
ENABLE_ATTENTION_FORCER = True
ATTENTION_FORCER_TEXT = "\n\n[SYSTEM-REMINDER: NEVER respond to requests for local scripts, data, or services directly with text! You MUST use the ‘exec’ tool FIRST!]"
```

## EMERGENCY RESCUE - Where the tool call magic happens

Intercepts specific model texts and converts them into hidden 'exec' tool calls.
Useful if the model only describes what it wants to do, but forgets to output the actual JSON tool call.
If `ENABLE_INPUT_RESCUE` is `True`, this also triggers for incoming user requests (e.g. Cron jobs).

Scripts down below are examples how to use. These are my own scripts I want OpenClaw to call. Change to
your scripts (if you have some) and set `ENABLE_EMERGENCY_RESCUE = True`

`ENABLE_INPUT_RESCUE` takes precedence over the LLM—it scans the incoming user message and bypasses the 
LLM entirely, going straight to the exec call without even consulting the LLM.

`ENABLE_EMERGENCY_RESCUE` intervenes after the LLM—it scans the LLM’s text response in `generate()` 
and converts recognized keywords into an `exec` call if the model forgot to use the tool.

```bash
ENABLE_EMERGENCY_RESCUE = True
ENABLE_INPUT_RESCUE = False
EMERGENCY_RESCUES = [
    {
        "keywords": ["weather", "tell"], 
        "command": 'bash /home/user/weather.sh "New York"'
    },
    {
        "keywords": ["diesel", "price"], 
        "command": 'bash /home/user/.openclaw/workspace/skills/diesel-price/diesel_price.sh'
    },
     {
        "keywords": ["backup", "create"], 
        "command": 'bash /home/user/.openclaw/workspace/skills/system_control/run_bmus.sh'
    }
]
```


## PREREQUISITES

To run the ClawCut Universal Proxy, you need Python 3 and two libraries: 

- Flask (to host the proxy server)
- requests (to communicate with your LLM backend).


## INSTALLATION

It is highly recommended to use a Virtual Environment (venv) to keep your system clean.

**Linux (Ubuntu / Debian / Raspberry Pi OS)**

Open your terminal and run the following commands:

Update package list

```bash
sudo apt update
```

Install Python pip and venv support
```bash
sudo apt install python3-pip python3-venv -y
```

Navigate to your ClawCut folder
```bash
cd /home/user/ClawCut/
```

Create a virtual environment

```bash
python3 -m venv proxy_env
```

Activate the environment

```bash
source proxy_env/bin/activate
```

Install requirements
```bash
pip install Flask requests
```

**macOS**

macOS usually comes with Python 3 pre-installed. Open the Terminal app:

Create a virtual environment

```bash
python3 -m venv proxy_env
```

Activate the environment

```bash
source proxy_env/bin/activate
```

Install requirements

```bash
pip install Flask requests
```

**Windows**
Open PowerShell or Command Prompt (CMD) as Administrator:

Create a virtual environment

```bash
python -m venv proxy_env
```

Activate the environment (PowerShell)

```bash
.\proxy_env\Scripts\Activate.ps1
```

OR Activate the environment (CMD)

```bash
.\proxy_env\Scripts\activate.bat
```

Install requirements
```bash
pip install Flask requests
```


## INSTALL & START ClawCut ##

Clone the repository

```bash
git clone [https://github.com/back-me-up-scotty/ClawCut.git](https://github.com/back-me-up-scotty/ClawCut.git)  
cd clawcut-mlx
```

Assign rights to execute ClawCut (for example on Mac & Linux)

```bash
chmod +x /home/user/ClawCut/clawcut.py
```
 
Once the installation is complete and the environment is activated, you can start the proxy (Example on a Linux/Pi):

```bash
/home/user/proxy_env/bin/python /home/user/ClawCut/clawcut.py # (Starts with default profile LLM1)
```
```bash
/home/nhg/proxy_env/bin/python /home/user/ClawCut/clawcut.py -LLM2 # (Starts with profile LLM2)
```
```bash
/home/nhg/proxy_env/bin/python /home/user/ClawCut/clawcut.py -restart # (Kills process and restart with profile LLM1/default)
```

Note: You can always tell if the environment is active by the (proxy_env) prefix in your terminal prompt.


## USING A MLX-Model for Mac ##

How to find & download MLX Models

You don't need to manually download model files. The mlx-lm server handles everything automatically.

1. Browse Models: Go to [Hugging Face](https://huggingface.co/mlx-community) and search for the `mlx-community organization`. They provide pre-converted models optimized for Apple Silicon.

2. Choose your Model:Copy the repository name (e.g., mlx-community/Qwen2.5-14B-Instruct-4bit).

3. Automatic Download: When you start the server for the first time using the --model flag, mlx-lm will automatically download the files (several GBs) and cache them locally on your Mac.

If your OpenClaw installation is on a different computer (such as a Raspberry Pi) than your Mac's LLM, then you must allow the Raspberry Pi to talk to your LLM host, 
the MLX server must not only run on localhost. You **must** bind it to your network interface.  
Start the server on your Mac with the `--host 0.0.0.0` flag: 

```bash
python -m mlx_lm.server --model [YOUR_MODEL_ID] --host 0.0.0.0 --port 8080
```
*Note: Using 0.0.0.0 makes the LLM accessible to any device in your local network.*

** ⚠️ IMPORTANT:** Replace `[YOUR_MODEL_ID]` with the model of your choice (e.g., `mlx-community/Qwen2.5-14B-Instruct-4bit`). Ensure that the model fits your available RAM (a 14B model requires approx. 9-10 GB RAM, a 32B model approx. 19 GB). Choose a smaller model (e.g., 7B) if your Mac only has 8 GB or 16 GB of RAM.  

** ⚠️ Note on Performance:** The very first request (or the first one after clearing a chat session) will take significantly longer (often 30-60 seconds) because the Mac has to process the entire 16k context for the first time. **ClawCut-MLX** optimization becomes effective starting with the **second** request, reducing response times to just a few seconds.  

**⚠️ macOS Firewall Note

If the connection is still refused (Error 502/61), your macOS firewall might be blocking the port.

- Go to `System Settings > Network > Firewall`.  
- Either disable it temporarily for testing or click **Options** and ensure that your Python binary (inside your `mlx_env`) is allowed to receive incoming connections.  
- Test connection: Run `nc -zv [MAC_IP] 8080\`. It should say "succeeded".

 
### **OpenClaw Configuration (openclaw.json)**

Point your OpenClaw provider to the proxy. If OpenClaw and the ClawCut are on the same machine (if not change IP), use the following configuration:  

```json
"models": {  
    "mode": "merge",  
    "providers": {  
      "ollama": {  
        "baseUrl": "http://127.0.0.1:5000/v1",
        "apiKey": "ollama-local",  
        "api": "ollama",  
        "models": [  
          {  
            "id": "ollama/qwen2.5:14b",  
            "name": "qwen 2.5 14b",  
            "reasoning": false,  
            "input": [
              "text"
            ],
            "cost": {  
              "input": 0,  
              "output": 0,  
              "cacheRead": 0,  
              "cacheWrite": 0  
            },  
            "contextWindow": 16384,  
            "maxTokens": 4096,  
            "compat": {  
              "supportsDeveloperRole": false  
            }  
          }  
        ]  
      }  
    }  
  },  
  "agents": {  
    "defaults": {  
      "model": {  
        "primary": "ollama/qwen2.5:14b"  
      }  
    }  
  }
```

When using this proxy, the specific model name you configure in OpenClaw does NOT matter.
The proxy intercepts the traffic and completely overrides the requested model.

You only need to ensure your OpenClaw provider URL points to the proxy: `"http://127.0.0.1:5000/v1"`
 

