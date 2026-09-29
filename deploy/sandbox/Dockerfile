# Image du bac à sable d'analyse de données de l'Assistant Opti.
# Construction : docker build -t opti-sandbox:1 /opt/assistant-opti/deploy/sandbox
FROM python:3.12-slim
RUN pip install --no-cache-dir "pandas>=2.2" "numpy>=1.26" "openpyxl>=3.1" \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox
USER 10001
WORKDIR /tmp
ENV PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp
