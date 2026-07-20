FROM python:3.11-slim

# Impede a gravação de arquivos .pyc em disco
ENV PYTHONDONTWRITEBYTECODE=1
# Mantém o stdout/stderr desprotegido (logs em tempo real)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Dependências Python.
# NÃO instalamos build-essential/gcc: todas as libs deste projeto distribuem
# wheels prontas para linux (psycopg2-binary, uvicorn[standard]/uvloop/httptools,
# pydantic-core). O apt-get install de compilador fazia o build estourar a
# memória do VPS (OOM -> "Killed" / "context canceled") depois que o container
# WAHA passou a dividir a máquina.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copia o projeto (ver .dockerignore — .venv/.git/tests/docs ficam de fora)
COPY . .

# Expõe a porta do servidor
EXPOSE 8000

# Endpoint da imagem
# alembic upgrade head já é chamado pelo lifespan do FastAPI na inicialização
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
