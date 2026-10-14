# vLLM server for AON

Tested on one RTX 4090 (24 GB). Replace `YOUR_USER` and `GPU_BOX_IP` with your own values.

| Here | On the GPU box |
|---|---|
| `systemd/vllm.service` | `/etc/systemd/system/vllm.service` (live: 16K context, thinking on) |
| `QWEN-config/` | `~/QWEN` without the weights (`model.safetensors` 18.6 GB, `model_mtp.safetensors` 0.8 GB) |
| `vllm-env-requirements.txt` | `pip freeze` of `~/vllm-env` (vLLM 0.31.0) |

`QWEN-config/chat_template.full.jinja` is the template the service starts with (`--chat-template`).
It came from the `MurtazaNasir/Qwen3.8-27B-OBLITERATED-AWQ-W4A16` repo on Hugging Face, because the
folder's own `chat_template.jinja` (506 bytes) has no tool support. `preprocessor_config.json` and
`video_preprocessor_config.json` came from the same repo.

## Restore on a fresh machine

1. Put the weights and these config files in `/home/YOUR_USER/QWEN`.
2. Create the venv: `uv venv ~/vllm-env && ~/vllm-env/bin/pip install -r vllm-env-requirements.txt`.
3. `sudo cp systemd/vllm.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now vllm`.
4. Check it: `curl http://GPU_BOX_IP:8001/v1/models` should list `aon` with `max_model_len` 16384.

AON reaches it via `chat_base_url = "http://GPU_BOX_IP:8001/v1"` in `config.toml`.
