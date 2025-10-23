# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

WORKDIR /app

# Installer curl (pour UV) + psql client si besoin pour le debug
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Installer UV (gestionnaire de paquets moderne)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh 
ENV PATH="/root/.local/bin:${PATH}"

# Copier uniquement les fichiers de dépendances pour profiter du cache
COPY pyproject.toml uv.lock ./

# Installer les dépendances déclarées dans le lockfile
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Copier le code source (les données lourdes sont exclues par .dockerignore)
COPY src/ ./src/

# Exposer le port FastAPI
EXPOSE 8000

# Commande par défaut (tu peux la surcharger dans ton GitHub Workflow ou docker-compose)
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
