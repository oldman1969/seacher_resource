FROM python:3.12-slim

WORKDIR /app

# 依赖层（利用 Docker 缓存；python-dotenv 是 config.py 的运行时依赖）
RUN pip install --no-cache-dir \
    "fastapi>=0.110" \
    "uvicorn[standard]>=0.29" \
    "httpx[http2]>=0.27" \
    "pydantic>=2.7" \
    "pydantic-settings>=2.3" \
    "python-dotenv>=1.0" \
    "pyyaml>=6.0" \
    "Brotli>=1.1"

# 只复制运行所需目录（refer/、tests/、scripts/ 不进镜像，尤其 refer/ 含密钥）
COPY app/ ./app/
COPY static/ ./static/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
