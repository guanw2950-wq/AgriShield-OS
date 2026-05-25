FROM python:3.11-slim

WORKDIR /app

# 系统依赖 (geopandas 需要 GDAL)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY api_gateway/requirements.txt /app/api_requirements.txt
COPY space_engine/requirements.txt /app/space_requirements.txt
RUN pip install --no-cache-dir --break-system-packages \
    -r /app/api_requirements.txt \
    -r /app/space_requirements.txt

# 源码
COPY api_gateway/ /app/api_gateway/
COPY space_engine/ /app/space_engine/

WORKDIR /app/api_gateway
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
