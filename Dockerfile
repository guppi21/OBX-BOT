FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1     PYTHONDONTWRITEBYTECODE=1     PIP_NO_CACHE_DIR=1     PORT=8000

WORKDIR /app

# Install system runtime & compilation dependencies
RUN apt-get update && apt-get install -y --no-install-recommends     gcc     libpq-dev     curl     ca-certificates     && rm -rf /var/lib/apt/lists/*

# Install python dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --upgrade pip &&     pip install -r requirements.txt

# Copy full application
COPY . .

# Run bot
CMD ["python", "apps/obx_tasks/bot/main.py", "--mode", "live"]
