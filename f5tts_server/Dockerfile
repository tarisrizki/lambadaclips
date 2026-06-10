FROM pytorch/pytorch:2.4.0-cuda12.4-cudnn9-devel

RUN apt-get update && apt-get install -y git ffmpeg wget

WORKDIR /workspace

# Clone F5-TTS
RUN git clone https://github.com/SWivid/F5-TTS.git
WORKDIR /workspace/F5-TTS

# Install dependencies without reinstalling torch
RUN grep -rl "torch" . | xargs sed -i '/torch/d' || true
RUN pip install --no-cache-dir -e .
RUN pip install --no-cache-dir fastapi uvicorn python-multipart huggingface_hub

# Download the model weights
RUN python -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='Eempostor/F5-TTS-INDO-FINETUNE-V2', filename='f5_tts_indo_v2.pt', local_dir='/workspace/model')"
RUN python -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='Eempostor/F5-TTS-INDO-FINETUNE-V2', filename='vocab.txt', local_dir='/workspace/model')" || echo "No vocab.txt in repo, using default"

COPY server.py /workspace/F5-TTS/server.py

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
