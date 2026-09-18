FROM python:3.9.13-slim-bullseye@sha256:b3bb514565b331c9a6f36c396af2a937f730b64f34004b750a3b3d5874abd990

# This digest and the PyTorch wheel are linux/amd64 only. No mutable apt step.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
COPY env/release-era/requirements.lock /opt/runner/requirements.lock
RUN python -m pip install --no-cache-dir --no-compile --only-binary=:all: --require-hashes \
    -r /opt/runner/requirements.lock && python -m pip check

ADD --checksum=sha256:df90d3c95298d02812dd518da43355b622e25091113a7a987ad999704b08aa7b \
    https://codeload.github.com/torchsynth/torchsynth/tar.gz/2b0964d4c6c3d472a2a0d54d91b408caaeffca6d /tmp/torchsynth.tar.gz
RUN mkdir /opt/torchsynth && tar -xzf /tmp/torchsynth.tar.gz \
    --strip-components=1 -C /opt/torchsynth && rm /tmp/torchsynth.tar.gz
COPY spec/reference/upstream.json /opt/runner/upstream.json
COPY env/release-era/ /opt/runner/
WORKDIR /opt/runner
ENTRYPOINT ["python", "/opt/runner/probe.py"]
