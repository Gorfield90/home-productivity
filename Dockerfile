FROM python:3.13-slim AS build
WORKDIR /src
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.13-slim
ARG HP_VERSION=dev
RUN useradd --uid 1000 --create-home app && mkdir /data && chown app /data
COPY --from=build /install /usr/local
USER app
WORKDIR /home/app
ENV HP_DATABASE_URL=sqlite:////data/home.db \
    HP_VERSION=${HP_VERSION} \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
