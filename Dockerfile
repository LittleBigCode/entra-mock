# Dedicated, non-root, minimal image — same discipline as the other mocks of
# the ecosystem (boondmanager-mock, linkedin-mock, ga-mock).
#
# `python:3.12-slim` pulled directly rather than through the Harbor proxy:
# this image must ALSO build outside the VPN (dev laptops, GitHub Actions),
# where harbor.build.graal.systems is unreachable. In-cluster consumers pull
# the published GHCR image THROUGH the Harbor ghcr-proxy, which keeps the
# Kyverno `only-harbor-images` policy satisfied cluster-side.
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN pip install --no-cache-dir uv

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# `--no-editable` : sans cette option uv installe le projet en mode éditable,
# soit un lien vers /build/src — qui n'existe pas dans l'étage final. L'image
# se construit alors très bien et échoue au démarrage sur
# "No module named entra_mock".
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --gid 65532 mock && \
    useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin mock

COPY --from=builder /opt/venv /opt/venv

USER 65532:65532
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["python", "-m", "entra_mock"]
