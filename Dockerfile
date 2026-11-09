FROM python:3.11-slim

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-local.txt ./
RUN pip install --no-cache-dir -r requirements-local.txt \
    && pip install --no-cache-dir --no-deps rapidocr_onnxruntime==1.4.4
COPY auto_check_in_ikuuu.py local_captcha.py captcha_images.py geetest_protocol.py GeekedTest.LICENSE ./
RUN useradd --system --uid 10001 --home-dir /app checkin \
    && install -d -o checkin -g checkin /app/data
USER checkin
ENV PYTHONUNBUFFERED=1 \
    IKUUU_CAPTCHA_SOLVER_ENABLED=1 \
    IKUUU_CAPTCHA_PROVIDER=local \
    IKUUU_DISABLE_SELF_UPDATE=1 \
    IKUUU_DATA_DIR=/app/data
VOLUME ["/app/data"]
CMD ["python", "auto_check_in_ikuuu.py"]
