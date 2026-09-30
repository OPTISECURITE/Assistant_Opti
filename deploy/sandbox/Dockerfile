# Image du bac à sable de l'Assistant Opti : analyse de données (pandas), graphiques (matplotlib), création de fichiers
# Excel / Word / PDF, et lecture des PDF (texte, tableaux, OCR des pages scannées).
# Construction : docker build -t opti-sandbox:3 /opt/assistant-opti/deploy/sandbox
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fra poppler-utils \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "pandas>=2.2" "numpy>=1.26" "openpyxl>=3.1" "pdfplumber>=0.11" \
        "matplotlib>=3.8" "python-docx>=1.1" "reportlab>=4.0" \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox
USER 10001
WORKDIR /tmp
ENV PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp MPLBACKEND=Agg
