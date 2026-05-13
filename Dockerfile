# ── Avaya Validation Framework — Render Deployment ──
# Multi-runtime: Node.js 18 + Python 3.11 + Playwright Chromium

FROM node:18-slim

# Install Python, pip, and system dependencies for Playwright
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    wget \
    curl \
    gnupg \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libxshmfence1 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libcups2 \
    libatspi2.0-0 \
    libxfixes3 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy package files and install Node dependencies
COPY package*.json ./
RUN npm install --production

# Install Python dependencies
RUN pip3 install --break-system-packages \
    pandas \
    openpyxl \
    playwright \
    requests \
    aiohttp \
    beautifulsoup4

# Install Playwright Chromium browser
RUN npx playwright install chromium
RUN python3 -m playwright install chromium

# Copy project files
COPY . .

# Create required directories
RUN mkdir -p .ui_reports .temp_csv reports auth-sessions PDF/prod PDF/stage

# Expose port (Render sets PORT env var)
EXPOSE 3000

# Start the server
CMD ["npx", "tsx", "scripts/server.ts"]
