#!/bin/bash
set -e

# Install system dependencies needed for Pillow and OpenCV
apt-get update
apt-get install -y \
    build-essential \
    python3-dev \
    libopenjp2-7 \
    libtiff6 \
    libwebp6 \
    libxcb1 \
    libxkbcommon0 \
    libxrender1 \
    libjpeg-turbo-progs \
    zlib1g

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt
