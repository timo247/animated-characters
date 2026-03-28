FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

# Dépendances système
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Python par défaut
RUN ln -s /usr/bin/python3 /usr/bin/python

# Upgrade pip
RUN pip install --upgrade pip

# ⚠️ IMPORTANT: NumPy < 2
RUN pip install numpy==1.26.4

# PyTorch GPU (CUDA 11.8)
RUN pip install torch==2.1.2+cu118 torchvision==0.16.2+cu118 torchaudio==2.1.2 \
    --index-url https://download.pytorch.org/whl/cu118

# WhisperX
RUN pip install git+https://github.com/m-bain/whisperx.git

WORKDIR /app

CMD ["bash"]

