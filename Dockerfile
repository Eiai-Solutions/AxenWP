FROM python:3.11-slim

# Impede a gravação de arquivos .pyc em disco
ENV PYTHONDONTWRITEBYTECODE 1
# Mantém o stdout/stderr desprotegido (logs em tempo real)
ENV PYTHONUNBUFFERED 1

WORKDIR /app

# Instala ferramentas base
RUN apt-get update \
    && apt-get -y install git build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Instala dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copia o projeto inteiro
COPY . .

# Expõe a porta do servidor
EXPOSE 8000

# Endpoint da imagem
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
