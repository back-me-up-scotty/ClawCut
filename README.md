## ClawCut Proxy

OpenClaw is a powerful framework that, by default, sends massive system prompts 
(often >28,000 characters) and complex tool definitions (JSON tools) to the LLM. 
While large cloud models or high-end local models (14B etc.) handle this well, 
small models (7B, 8B) running on limited hardware (Mac/MLX or Raspberry Pi) 
often suffer from "Cognitive Overload". This is where ClawCut steps in.

ClawCut is an experimental proxy to manipulate, inject JSON-Calls and 
extract JSON clutter from OpenClaw. 

## BENEFITS OF USING CLAWCUT:
 
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
  and format translator without manipulating the content if PASS_THROUGH_MODE = True.

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
        "model_id": 
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
"http://127.0.0.1:5000/v1"


**Logging & Storage Config**

DEBUG_MODE = True prints the full JSON payloads to the console (useful for troubleshooting).
WRITE_TO_LOGFILE saves the terminal output to the specified PATH_TO_LOGFILE.
DELETE_LOG_SIZE rotates/deletes the log automatically when it reaches this size to prevent disk full issues.

Location/path to logs. Examples:
 
Linux/Pi: "/home/username/" 
Mac: "/Users/username/"
Windows: "C:/Users/username/"

```bash
DEBUG_MODE = True
WRITE_TO_LOGFILE = True
PATH_TO_LOGFILE = '/home/nhg/clawcut.log' # Change to your preferred log path
DELETE_LOG_SIZE = '10 MB' 
```

## SMART AMNESIA MODE

Over time, chat histories get too long for small models to process efficiently.
If True, the proxy watches for tool calls (specifically the 'exec' tool). 
Once a tool has been successfully executed, the proxy truncates all chat history 
prior to that execution. This creates a "fresh start" while keeping the final results,
preventing infinite loops and keeping the context window and RAM small.

```bash
ENABLE_SMART_AMNESIA = True
```

## CHAT HISTORY

Amnesia only when the current turn processes a tool result (last message is ‘tool’)
Example: You exchanged 5 messages without calling a tool. The context is preserved. 
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

Important: Since OpenClaw version 2026.3.12 there are issues with the routing of messages triggered by a cron job. 
ClawCut clearly sees this messages. The issue seems to be on OpenClaw's side. FORCE_CRON_DELIVERY has unfortunately 
no effect at the moment. OpenClaw ignores it.



```bash
EXPECTED_SCRIPT_BASE_PATH = "/home/user/"
```

Attention Forcer (End-of-Prompt Injection)

If True, this injects a strong reminder at the very end of the user's latest message.

```bash
ENABLE_ATTENTION_FORCER = True
ATTENTION_FORCER_TEXT = "\n\n[SYSTEM-REMINDER: NEVER respond to requests for local scripts, data, or services directly with text! You MUST use the ‘exec’ tool FIRST!]"
```

Emergency Rescue (Catch & Convert) - Where the tool call magic happens

Intercepts specific model texts and converts them into hidden 'exec' tool calls.
Useful if the model only describes what it wants to do, but forgets to output the actual JSON tool call.
If `ENABLE_INPUT_RESCUE` is `True`, this also triggers for incoming user requests (e.g. Cron jobs).

Scripts down below are examples how to use. These are my own script I want OpenClaw to call. Change to
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


## **Prerequisites**

To run the ClawCut Universal Proxy, you need Python 3 and two libraries: 

- Flask (to host the proxy server)
- requests (to communicate with your LLM backend).


## **Installation**

It is highly recommended to use a Virtual Environment (venv) to keep your system clean.

1. Linux (Ubuntu / Debian / Raspberry Pi OS)
Open your terminal and run the following commands:

# Update package list
sudo apt update

# Install Python pip and venv support
sudo apt install python3-pip python3-venv -y

# Navigate to your ClawCut folder
cd ~/ClawCut

# Create a virtual environment
python3 -m venv proxy_env

# Activate the environment
source proxy_env/bin/activate

# Install requirements
pip install Flask requests

2. macOS
macOS usually comes with Python 3 pre-installed. Open the Terminal app:

# Create a virtual environment
python3 -m venv proxy_env

# Activate the environment
source proxy_env/bin/activate

# Install requirements
pip install Flask requests

3. Windows
Open PowerShell or Command Prompt (CMD) as Administrator:

# Create a virtual environment
python -m venv proxy_env

# Activate the environment (PowerShell)
.\proxy_env\Scripts\Activate.ps1

# OR Activate the environment (CMD)
# .\proxy_env\Scripts\activate.bat

# Install requirements
pip install Flask requests

How to install and start the ClawCut

Clone the repository

```bash
git clone [https://github.com/back-me-up-scotty/ClawCut.git](https://github.com/back-me-up-scotty/ClawCut.git)  
cd clawcut-mlx
```
Assign rights to execute ClawCut (for example on Mac & Linux)

```bash
chmod +x /home/user/clawcu/clawcut.py
```
 
Once the installation is complete and the environment is activated, you can start the proxy:

```bash
python clawcut-mlx.py # (Starts with default profile LLM1)
```
```bash
python clawcut-mlx.py -LLM2 # (Starts with profile LLM2)
```
```bash
python clawcut-mlx.p -restart # (Kills process and restart with profile LLM1/default)
```

Note: You can always tell if the environment is active by the (proxy_env) prefix in your terminal prompt.


USING A MLX-Model for Mac

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

⚠️ **IMPORTANT:** Replace `[YOUR_MODEL_ID]` with the model of your choice (e.g., `mlx-community/Qwen2.5-14B-Instruct-4bit`). Ensure that the model fits your available RAM (a 14B model requires approx. 9-10 GB RAM, a 32B model approx. 19 GB). Choose a smaller model (e.g., 7B) if your Mac only has 8 GB or 16 GB of RAM.  

⚠️ **Note on Performance:** The very first request (or the first one after clearing a chat session) will take significantly longer (often 30-60 seconds) because the Mac has to process the entire 16k context for the first time. **ClawCut-MLX** optimization becomes effective starting with the **second** request, reducing response times to just a few seconds.  

#### **⚠️ macOS Firewall Note**

If the connection is still refused (Error 502/61), your macOS firewall might be blocking the port.

* Go to **`System Settings > Network > Firewall`**.  
* Either disable it temporarily for testing or click **Options** and ensure that your Python binary (inside your `mlx_env`) is allowed to receive incoming connections.  
* **Test connection from Pi:** Run `nc -zv [MAC_IP] 8080\`. It should say "succeeded".

 
### **OpenClaw Configuration (openclaw.json)**

Point your OpenClaw provider to the proxy. If OpenClaw and the Proxy are on the same Pi, use the following configuration:  

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

