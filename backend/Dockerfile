FROM python:3.13-slim

WORKDIR /app

# Install deps separately from the source copy so the (slow) pip layer stays
# cached across code-only changes. This list mirrors pyproject's
# [agent,rag,ssh] extras; keep them in sync.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
      "fastapi>=0.115" "uvicorn[standard]>=0.30" "python-dotenv>=1.0" \
      "pydantic>=2.0" "itsdangerous>=2.2" \
      "litellm>=1.50" "langgraph>=0.2" \
      "langchain-core>=0.3" "langchain-litellm>=0.1" \
      "chromadb>=0.5" \
      "paramiko>=3.0"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
