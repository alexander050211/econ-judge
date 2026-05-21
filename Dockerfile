# Auto-grader deployment image: CTFd 3.8.5 + econ_judge plugin + Java 17 + Digital.jar.
# Used by Render (or any container host) to deploy the working state.
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless \
        git \
        curl \
        ca-certificates \
        unzip \
    && rm -rf /var/lib/apt/lists/*

ENV CTFD_VERSION=3.8.5
WORKDIR /opt
RUN git clone --depth 1 --branch ${CTFD_VERSION} https://github.com/CTFd/CTFd.git

WORKDIR /opt/CTFd
RUN pip install -r requirements.txt && pip install requests gunicorn

ENV DIGITAL_VERSION=v0.31
RUN curl -sSL -o /tmp/Digital.zip \
        "https://github.com/hneemann/Digital/releases/download/${DIGITAL_VERSION}/Digital.zip" \
    && unzip -q /tmp/Digital.zip -d /tmp/digital \
    && mkdir -p /opt/econ-judge \
    && cp /tmp/digital/Digital/Digital.jar /opt/econ-judge/Digital.jar \
    && rm -rf /tmp/Digital.zip /tmp/digital

COPY econ_judge /opt/CTFd/CTFd/plugins/econ_judge
COPY secret_tests /opt/econ-judge/secret_tests
COPY canonical /opt/econ-judge/canonical
COPY tests/register_challenges.py /opt/econ-judge/tests/register_challenges.py
COPY bin /opt/econ-judge/bin
RUN chmod +x /opt/econ-judge/bin/entrypoint.sh

ENV ECON_JUDGE_DIGITAL_JAR=/opt/econ-judge/Digital.jar \
    ECON_JUDGE_TESTS_DIR=/opt/econ-judge/secret_tests \
    ECON_JUDGE_CANONICAL_DIR=/opt/econ-judge/canonical \
    PYTHONPATH=/opt/CTFd:/opt/econ-judge

EXPOSE 8000
ENTRYPOINT ["/opt/econ-judge/bin/entrypoint.sh"]
