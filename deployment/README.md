# MobiAgent Server

## Run with a cloud vision model on Windows

This mode sends screenshots and tasks from the Android app to a local server, which forwards them to one OpenAI-compatible cloud model. In cloud mode, action selection and element grounding share one request so each screenshot needs only one model call. It does not download or run model weights locally.

Edit `cloud-config.json` in the repository root to set `api_key`, `base_url`, `model`, and `wire_api` (`responses` or `chat_completions`). For a fresh checkout, create it from `cloud-config.example.json` first. The local `cloud-config.json` contains the API key in plain text and is ignored by Git; keep it private.

Double-click `start-cloud.cmd`, or start the server in PowerShell:

```powershell
.\deployment\run_cloud.ps1
```

The launcher reads the configuration file without prompting for an API key. Keep that terminal open while using the app; press Ctrl+C to stop the server. Restart the server after changing the configuration.

Cloud connections default to direct HTTPS with `use_env_proxy: false`, so an inherited desktop proxy does not affect this server. Set `use_env_proxy` to `true` to use `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` from the launching terminal. TLS certificate verification remains enabled. Connection failures and timeouts stop the current task with a message in the app; existing memories are kept.

The script creates an isolated `.venv` and listens on `127.0.0.1:2333`. The supplied configuration uses `gpt-6.1-sol` through the Responses API with `store=false`. The launcher passes the configured key to the server through its process environment. Cloud requests time out after 25 seconds without automatic retries to fit the Android app's 30-second response timeout. Cloud latency may still cause a task to fail.

Personal memory is enabled by default through `memory_enabled` and `memory_dir` in the configuration. See [the memory guide](MEMORY_zh.md) for examples, persistence, and forgetting. Memory understanding and app selection use one text request at the start of a task; screenshot decisions reuse the resolved task and relevant facts. The existing Android app does not need rebuilding.

For an Android emulator, forward the local server port with ADB and configure the app's server IP as `127.0.0.1` and port as `2333`:

```powershell
.\.local\adb\adb.exe -s emulator-5558 reverse tcp:2333 tcp:2333
```

This checkout includes a private copy of the already installed LDPlayer ADB in `.local/adb/`; it does not change the global PATH or install Android Studio. The launcher automatically sets up forwarding when exactly one device is connected. With multiple devices, use `.\deployment\run_cloud.ps1 -Device <serial>`. Forwarding must be recreated after a device or ADB restart, which the launcher does for you.

The detected emulator is `emulator-5558`, and its ADB transport listener is port `5559`. Port `5037` is the PC ADB server; `2333` is the MobiAgent HTTP service. Do not put either ADB port into the app's server settings.

The original app/server supports only a fixed list of apps and actions. A cloud vision model can recognize images without necessarily controlling every UI accurately; start with a simple reversible task.

The bundled server defaults to binding all interfaces for the original deployment mode. The cloud launcher explicitly binds to `127.0.0.1` so the unauthenticated phone-control endpoint stays local.

## Deploy MobiMind Models with vLLM

```bash
vllm serve IPADS-SAI/MobiMind-Decider-7B --port <decider port>
vllm serve IPADS-SAI/MobiMind-Grounder-3B --port <grounder port>
vllm serve Qwen/Qwen3-4B-Instruct --port <planner port>
```

## Run Server

```bash
python -m mobiagent_server.server \
    --port <server port> \
    --decider_url <base url for decider model> \
    --grounder_url <base url for grounder model> \
    --planner_url <base url for planner model> \
    --use_qwen3 <whether to use MobiMind Qwen3-VL model series, e.g., MobiMind-Mixed-4B>
```

Then you can set MobiAgent Server IP and port in the MobiAgent App, and start exploration!
