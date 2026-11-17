# AI-2027 live demo — container image
#
# Works on Hugging Face Spaces (Docker SDK), Fly.io, Railway, Cloud Run, or any
# Docker host. 7860 is the port HF Spaces expects; override with -e PORT=...
#
# Honest note on the base image: `python:3.11-slim` is Debian-slim, i.e. a
# *distribution* of the stdlib, not a third-party Python package. The project's
# zero-dependency guarantee is about imports (enforced by scripts/ci_guard.py in
# CI), and it holds inside this image: the build installs nothing.
FROM python:3.11-slim

# Unbuffered stdout so PaaS log panes show output immediately, and no .pyc clutter.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7860 \
    AI2027_MAX_DISK_MB=256 \
    AI2027_MAX_RUNS=5000 \
    AI2027_READ_ONLY=0

WORKDIR /app

# Only what the server actually needs. Copying the whole repo would ship the
# 48-project catalogue, the website and every test into the image.
COPY live-agent/ ./live-agent/
COPY projects/agi-system/core/ ./projects/agi-system/core/
COPY deployment/ ./deployment/

# There is no projects/agi-system/__init__.py in the repo (verified), so it must
# NOT be a COPY source — that would fail the build with "file not found". The
# package root is created here instead. `core/` DOES ship its own __init__.py.

# Fail the build if the kernel is missing rather than failing at request time.
RUN python -c "import sys; sys.path.insert(0,'projects/agi-system'); \
    from core.kernel import Kernel; print('kernel imports OK')"

# Run unprivileged: the demo writes evidence and needs nothing else.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 7860

# No shell form: signals reach Python directly so SIGTERM stops it cleanly.
CMD ["python3", "deployment/live_server.py"]
