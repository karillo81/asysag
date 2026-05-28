FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30" "python-dotenv>=1.0" "pydantic>=2.0"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
